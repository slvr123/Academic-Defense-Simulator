"""FastAPI app for the v2.0 backend (M1). Run with exactly one worker:

    uvicorn backend.app:app --host 0.0.0.0 --port $PORT --workers 1

Each uvicorn worker is a separate process with its own model copy and its own
in-memory sessions, so a second worker would double memory and split sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from backend.embedder import Embedder, OnnxEmbedder
from backend.ingest_queue import IngestJob, IngestQueue, QueueFullError
from backend.pdf_ingest import UploadRejected, check_pages, reset_upload_dir, spool_upload
from backend.schemas import HealthResponse, PanelConfig, SessionCreated, SessionState
from backend.sessions import SessionCapacityError, SessionStatus, SessionStore
from backend.settings import Settings

logger = logging.getLogger("backend")

BUSY_MESSAGE = "The panel is busy with other documents right now. Please try again in a few minutes."
NOT_FOUND_MESSAGE = "Session not found. It may have expired after 30 idle minutes, or been deleted."
# Multipart framing and the panel field ride on top of the file itself.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024

EmbedderFactory = Callable[[Settings], Embedder]


def _load_onnx(settings: Settings) -> Embedder:
    return OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)


def _import_backend_runtime() -> None:
    # google-genai is imported here so startup memory matches the real backend;
    # nothing calls it until M2 (Gemini question generation and scoring).
    import google.genai  # noqa: F401


def create_app(settings: Settings | None = None, embedder_factory: EmbedderFactory = _load_onnx) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        reset_upload_dir(settings.upload_dir)
        _import_backend_runtime()
        embedder = embedder_factory(settings)
        app.state.embedder = embedder
        app.state.store = SessionStore(settings.max_live_sessions, settings.session_ttl_seconds)
        app.state.queue = IngestQueue(embedder, settings.queue_max_waiting)
        tasks = [
            asyncio.create_task(app.state.queue.run(), name="ingest-worker"),
            asyncio.create_task(_sweep_forever(app.state.store, settings.sweep_interval_seconds), name="ttl-sweep"),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Academic Defense Simulator backend", lifespan=lifespan)
    app.state.embedder = None
    app.state.settings = settings

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(model_loaded=app.state.embedder is not None)

    @app.post("/sessions", status_code=202, response_model=SessionCreated)
    async def create_session(request: Request) -> SessionCreated:
        # The route reads the form itself, rather than declaring File/Form
        # parameters, so these cheap checks run before the body is received.
        store: SessionStore = app.state.store
        queue: IngestQueue = app.state.queue
        if not (store.has_capacity() and queue.has_room()):
            raise HTTPException(503, BUSY_MESSAGE)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes + _MULTIPART_OVERHEAD_BYTES:
            raise HTTPException(413, f"That PDF is larger than the {settings.max_upload_mb:g} MB limit.")

        async with request.form(max_files=1, max_fields=1) as form:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise HTTPException(422, "Attach the PDF as a multipart field named 'file'.")
            panel = _parse_panel(form.get("panel"))
            try:
                pdf_path = await asyncio.to_thread(
                    spool_upload, upload.file, settings.upload_dir, settings.max_upload_bytes
                )
            except UploadRejected as exc:
                raise HTTPException(exc.status_code, exc.message) from exc
        # From here the spooled file is ours to delete on any failure; once
        # submitted, the worker owns it and deletes it when the job ends.
        try:
            try:
                await asyncio.to_thread(check_pages, pdf_path, settings.max_pages)
            except UploadRejected as exc:
                raise HTTPException(exc.status_code, exc.message) from exc

            # Authoritative capacity check. No await from here to the return, so
            # no other request can take the slot this one was just granted.
            if not queue.has_room():
                raise HTTPException(503, BUSY_MESSAGE)
            try:
                session = store.create(panel, upload.filename or "upload.pdf")
            except SessionCapacityError:
                raise HTTPException(503, BUSY_MESSAGE) from None
            try:
                position = queue.submit(IngestJob(session=session, pdf_path=pdf_path))
            except QueueFullError:  # unreachable after has_room() with no await; a guard
                store.delete(session.id)
                raise HTTPException(503, BUSY_MESSAGE) from None
        except BaseException:
            pdf_path.unlink(missing_ok=True)
            raise
        return SessionCreated(session_id=session.id, queue_position=position)

    @app.get("/sessions/{session_id}", response_model=SessionState, response_model_exclude_none=True)
    def get_session(session_id: str) -> SessionState:
        session = app.state.store.get(session_id)
        if session is None:
            raise HTTPException(404, NOT_FOUND_MESSAGE)
        return SessionState(
            status=session.status.value,
            queue_position=app.state.queue.position(session.id)
            if session.status is SessionStatus.QUEUED
            else None,
            progress=session.progress,
            chunk_count=session.chunk_count,
            error=session.error,
        )

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        session = app.state.store.delete(session_id)
        if session is None:
            raise HTTPException(404, NOT_FOUND_MESSAGE)
        app.state.queue.withdraw(session_id)
        return Response(status_code=204)

    return app


def _parse_panel(raw: object) -> PanelConfig:
    if raw is None or raw == "":
        return PanelConfig()
    if not isinstance(raw, str):
        raise HTTPException(422, "The 'panel' field must be JSON text, not a file.")
    try:
        return PanelConfig.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json(include_url=False, include_context=False))) from exc


async def _sweep_forever(store: SessionStore, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        evicted = store.sweep()
        if evicted:
            logger.info("ttl sweep evicted %d session(s)", len(evicted))


app = create_app()
