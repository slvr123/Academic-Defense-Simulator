"""FastAPI app for the v2.0 backend. Run with exactly one worker:

    uvicorn backend.app:app --host 0.0.0.0 --port $PORT --workers 1

Each uvicorn worker is a separate process with its own model copy and its own
in-memory sessions, so a second worker would double memory and split sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import asdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from backend import memory
from backend.defense import ProvidersFactory
from backend.client_ip import client_ip, forwarded_for
from backend.defense_routes import defense_status, gemini_providers_factory, register_defense_routes
from backend.embedder import Embedder, OnnxEmbedder
from backend.errors import CodedHTTPException, coded_http_exception_handler
from backend.ingest_queue import IngestJob, IngestQueue, QueueFullError
from backend.panel_options import panel_options
from backend.pdf_ingest import UploadRejected, check_pages, reset_upload_dir, spool_upload
from backend.quota import (
    PostgresQuotaStore,
    QuotaStore,
    QuotaUnavailable,
    log_plan,
    pacific_day,
    plan_quota,
    resets_at,
    warm_up,
)
from backend.schemas import (
    DebugClient,
    DebugMemory,
    ErrorBody,
    HealthResponse,
    LimitCode,
    PanelConfig,
    PanelOptions,
    QuotaStatus,
    SessionCreated,
    SessionState,
)
from backend.sessions import SessionCapacityError, SessionStatus, SessionStore
from backend.settings import Settings
from backend.tts import Synthesize
from backend.tts_clone import synthesize_for

logger = logging.getLogger("backend")

BUSY_MESSAGE = "The panel is busy with other documents right now. Please try again in a few minutes."
NOT_FOUND_MESSAGE = "Session not found. It may have expired after 30 idle minutes, or been deleted."
QUOTA_UNAVAILABLE_MESSAGE = "The demo's usage counter is waking up. Please try again in a few seconds."
# Multipart framing and the panel field ride on top of the file itself.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024

# POST /sessions reads its form by hand (see the route), so FastAPI can't describe the
# body itself. This documents it, and registers PanelConfig with the schema, so the
# frontend's generated types include it.
_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "panel": {
                            "type": "string",
                            "description": "PanelConfig as JSON text; omitted gives its defaults.",
                            "contentMediaType": "application/json",
                            "contentSchema": {"$ref": "#/components/schemas/PanelConfig"},
                        },
                    },
                }
            }
        },
    }
}

EmbedderFactory = Callable[[Settings], Embedder]
QuotaStoreFactory = Callable[[Settings], QuotaStore]
Clock = Callable[[], datetime]


def _postgres_store(settings: Settings) -> QuotaStore:
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not set; the daily quota guard needs Postgres")
    return PostgresQuotaStore(settings.database_url.get_secret_value())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def quota_used_up(
    now: datetime, message: str | None = None, code: LimitCode = "daily_cap", limit: int | None = None
) -> HTTPException:
    """429 until midnight Pacific: today's defenses are used up, or `message`'s reason.
    `code` tells the client which limit it hit without reading the message, and
    `limit` how many starts that limit allows (sent for per_ip_limit, M6 H6)."""
    retry_after = int((resets_at(pacific_day(now)) - now).total_seconds())
    return CodedHTTPException(
        429, message or quota_used_up_message(now), code, headers={"Retry-After": str(retry_after)}, limit=limit
    )


def quota_used_up_message(now: datetime) -> str:
    hours = max(1, round((resets_at(pacific_day(now)) - now).total_seconds() / 3600))
    return (
        "Today's demo defenses are all used up. "
        f"They reset at midnight Pacific time, in about {hours} hour{'s' if hours != 1 else ''}."
    )


def _load_onnx(settings: Settings) -> Embedder:
    return OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)


def _import_backend_runtime() -> None:
    # Imported at startup, not on the first defense, so the memory measured at
    # startup already includes it.
    import google.genai  # noqa: F401


def create_app(
    settings: Settings | None = None,
    embedder_factory: EmbedderFactory = _load_onnx,
    quota_store_factory: QuotaStoreFactory = _postgres_store,
    clock: Clock = _utc_now,
    providers_factory: Callable[[Settings], ProvidersFactory] = gemini_providers_factory,
    synthesize: Synthesize | None = None,
) -> FastAPI:
    settings = settings or Settings()
    # M8 K5: without an injected one, ADS_TTS_VOICE_MODE picks VoiceDesign or the cloned voices.
    synthesize = synthesize or synthesize_for(settings.tts_voice_mode)
    quota_plan = plan_quota(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _configure_logging()
        logger.info("memory at startup %s", memory.describe())
        log_plan(quota_plan)
        logger.info("tts voice_mode=%s split_sentences=%s", settings.tts_voice_mode, settings.tts_split_sentences)
        app.state.quota = quota_store_factory(settings)
        app.state.providers_factory = providers_factory(settings)
        reset_upload_dir(settings.upload_dir)
        _import_backend_runtime()
        logger.info("memory after runtime imports %s", memory.describe())
        started = time.perf_counter()
        embedder = embedder_factory(settings)
        app.state.embedder = embedder
        logger.info(
            "memory after model load %s load_s=%.2f", memory.describe(), time.perf_counter() - started
        )
        app.state.store = SessionStore(settings.max_live_sessions, settings.session_ttl_seconds)
        app.state.queue = IngestQueue(embedder, settings.queue_max_waiting)
        tasks = [
            asyncio.create_task(app.state.queue.run(), name="ingest-worker"),
            asyncio.create_task(_sweep_forever(app.state.store, settings.sweep_interval_seconds), name="ttl-sweep"),
            # Off the startup path: a cold Neon must not hold up /health.
            asyncio.create_task(asyncio.to_thread(warm_up, app.state.quota), name="quota-warm-up"),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            app.state.quota.close()

    app = FastAPI(title="Academic Defense Simulator backend", lifespan=lifespan)
    app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)
    # Only the browser frontend needs this. Exactly the methods and headers it uses;
    # Retry-After is exposed so the frontend can read it on a cross-origin 429 or 503.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Retry-After", "X-TTS-Parts"],
    )
    app.state.embedder = None
    app.state.settings = settings
    app.state.quota_plan = quota_plan
    app.state.last_chunk_copy = None  # set by /start, read by /debug/memory

    def health() -> HealthResponse:
        return HealthResponse(model_loaded=app.state.embedder is not None, tts_available=settings.mimo_key is not None)

    # EasyPrivacy blocks `||onrender.com/health` (M3b E2), so browsers call /status.
    # /health stays for Render's health check and anything already deployed.
    app.get("/status", response_model=HealthResponse)(health)
    app.get("/health", response_model=HealthResponse, deprecated=True)(health)

    @app.get("/panel-options", response_model=PanelOptions)
    def get_panel_options() -> PanelOptions:
        return panel_options()

    @app.post(
        "/sessions", status_code=202, response_model=SessionCreated,
        responses={429: {"model": ErrorBody}}, openapi_extra=_UPLOAD_BODY,
    )
    async def create_session(request: Request) -> SessionCreated:
        # The route reads the form itself, rather than declaring File/Form
        # parameters, so these cheap checks run before the body is received.
        store: SessionStore = app.state.store
        queue: IngestQueue = app.state.queue
        if not (store.has_capacity() and queue.has_room()):
            raise HTTPException(503, BUSY_MESSAGE)
        await _refuse_if_quota_used_up()
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
                page_count = await asyncio.to_thread(check_pages, pdf_path, settings.max_pages)
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
                position = queue.submit(
                    IngestJob(
                        session=session,
                        pdf_path=pdf_path,
                        page_count=page_count,
                        spooled=memory.snapshot("spooled"),
                    )
                )
            except QueueFullError:  # unreachable after has_room() with no await; a guard
                store.delete(session.id)
                raise HTTPException(503, BUSY_MESSAGE) from None
        except BaseException:
            pdf_path.unlink(missing_ok=True)
            raise
        return SessionCreated(session_id=session.id, queue_position=position)

    async def _refuse_if_quota_used_up() -> None:
        """The upload's non-reserving check: spare the user an upload and an embed
        when no defense could start today anyway. Fails open, because an upload
        spends no Gemini calls and starting the defense checks again."""
        now = clock()
        try:
            used = await asyncio.to_thread(app.state.quota.used, pacific_day(now))
        except QuotaUnavailable as exc:
            logger.warning("quota check skipped for an upload, store unreachable: %s", exc)
            return
        if used >= quota_plan.cap:
            raise quota_used_up(now)

    @app.get("/quota", response_model=QuotaStatus)
    def quota() -> QuotaStatus:
        day = pacific_day(clock())
        try:
            used = app.state.quota.used(day)
        except QuotaUnavailable as exc:
            raise HTTPException(503, QUOTA_UNAVAILABLE_MESSAGE) from exc
        return QuotaStatus(
            used=used, cap=quota_plan.cap, remaining=max(0, quota_plan.cap - used), resets_at=resets_at(day)
        )

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
            error_code=session.error_code,
            defense=defense_status(session),
        )

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        session = app.state.store.delete(session_id)
        if session is None:
            raise HTTPException(404, NOT_FOUND_MESSAGE)
        app.state.queue.withdraw(session_id)
        return Response(status_code=204)

    app.openapi = lambda: _openapi_with_panel_config(app)  # type: ignore[method-assign]

    register_defense_routes(
        app, settings=settings, quota_plan=quota_plan, clock=clock,
        not_found=lambda: HTTPException(404, NOT_FOUND_MESSAGE), quota_used_up=quota_used_up,
        synthesize=synthesize,
    )

    if settings.debug_client:
        # For checking, once on Render, which X-Forwarded-For entry is the real client.
        @app.get("/debug/client", response_model=DebugClient)
        def debug_client(request: Request) -> DebugClient:
            return DebugClient(
                peer=request.client.host if request.client else None,
                x_forwarded_for=forwarded_for(request),
                trusted_proxy_hops=settings.trusted_proxy_hops,
                resolved_ip=client_ip(request, settings.trusted_proxy_hops),
            )

    if settings.debug_memory:
        # Not registered at all otherwise, so the path is a plain 404.
        @app.get("/debug/memory", response_model=DebugMemory)
        def debug_memory() -> DebugMemory:
            last_job = app.state.queue.last_job
            hwm = memory.high_water_mb()
            return DebugMemory(
                pid=os.getpid(),
                process_started_at=memory.process_started_at(),
                commit=os.environ.get("RENDER_GIT_COMMIT"),  # set by Render on every deploy
                max_upload_mb=settings.max_upload_mb,
                max_pages=settings.max_pages,
                rss_mb=round(memory.rss_mb(), 1),
                hwm_mb=None if hwm is None else round(hwm, 1),
                last_job=asdict(last_job) if last_job else None,
                last_chunk_copy=app.state.last_chunk_copy,
            )

    return app


def _openapi_with_panel_config(app: FastAPI) -> dict[str, Any]:
    """FastAPI's schema, plus PanelConfig, which no route declares as a parameter."""
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema["components"]["schemas"]
        panel = PanelConfig.model_json_schema(ref_template="#/components/schemas/{model}")
        nested = panel.pop("$defs", {})
        missing = set(nested) - set(components)
        assert not missing, f"PanelConfig refers to schemas no route registered: {missing}"
        components["PanelConfig"] = panel
        app.openapi_schema = schema
    return app.openapi_schema


def _parse_panel(raw: object) -> PanelConfig:
    if raw is None or raw == "":
        return PanelConfig()
    if not isinstance(raw, str):
        raise HTTPException(422, "The 'panel' field must be JSON text, not a file.")
    try:
        return PanelConfig.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json(include_url=False, include_context=False))) from exc


def _configure_logging() -> None:
    # uvicorn configures only its own loggers; give backend.* a handler too.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("backend").setLevel(logging.INFO)


async def _sweep_forever(store: SessionStore, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        evicted = store.sweep()
        if evicted:
            logger.info("ttl sweep evicted %d session(s)", len(evicted))


app = create_app()
