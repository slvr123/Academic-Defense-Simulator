"""The embedding queue: one asyncio.Queue, one worker task, CPU work in a thread.

`IngestQueue.run()` is started once in the app's lifespan. It takes one job at a
time, so at most one document is ever being parsed and embedded; the parse and
embed run in `asyncio.to_thread` so the event loop keeps answering requests.

The wait cap is enforced on `_waiting` (session ids in arrival order), not with
`asyncio.Queue(maxsize=...)`: a waiting job that gets DELETEd leaves `_waiting`
at once, whereas it would hold a `maxsize` slot until the worker reached it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from backend.embedder import EMBEDDING_DIM, Embedder
from backend.pdf_ingest import IngestFailure, parse_pdf
from backend.sessions import ChunkMeta, Session, SessionStatus

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = "Something went wrong while processing this PDF. Try again, or try a different file."


@dataclass(frozen=True)
class IngestJob:
    session: Session
    pdf_path: Path


@dataclass(frozen=True)
class IngestResult:
    matrix: np.ndarray
    chunks: list[ChunkMeta]


class QueueFullError(Exception):
    """`max_waiting` jobs are already waiting."""


class IngestQueue:
    def __init__(self, embedder: Embedder, max_waiting: int) -> None:
        self._embedder = embedder
        self._max_waiting = max_waiting
        self._queue: asyncio.Queue[IngestJob] = asyncio.Queue()
        self._waiting: list[str] = []

    def has_room(self) -> bool:
        return len(self._waiting) < self._max_waiting

    def submit(self, job: IngestJob) -> int:
        """Enqueue without awaiting; returns the 1-based queue position."""
        if not self.has_room():
            raise QueueFullError
        self._queue.put_nowait(job)
        self._waiting.append(job.session.id)
        return len(self._waiting)

    def position(self, session_id: str) -> int | None:
        try:
            return self._waiting.index(session_id) + 1
        except ValueError:
            return None

    def withdraw(self, session_id: str) -> None:
        """A waiting session was deleted: free its place now. The worker still
        receives the job, sees it cancelled, and only removes its temp file."""
        if session_id in self._waiting:
            self._waiting.remove(session_id)

    async def run(self) -> None:
        while True:
            job = await self._queue.get()
            self.withdraw(job.session.id)
            try:
                await self._process(job)
            except Exception:  # never let one job stop the worker
                logger.exception("ingest worker: unhandled error for session %s", job.session.id)
            finally:
                self._queue.task_done()

    async def _process(self, job: IngestJob) -> None:
        session = job.session
        try:
            if session.cancelled:
                return
            session.status = SessionStatus.EMBEDDING
            session.progress = 0.0
            try:
                result = await asyncio.to_thread(self._ingest, job)
            except IngestFailure as exc:
                self._fail(session, str(exc))
                return
            except Exception:
                logger.exception("ingest failed for session %s", session.id)
                self._fail(session, UNEXPECTED_ERROR)
                return
            if result is None or session.cancelled:
                return
            session.matrix = result.matrix
            session.chunks = result.chunks
            session.progress = 1.0
            session.status = SessionStatus.READY
        finally:
            job.pdf_path.unlink(missing_ok=True)

    def _ingest(self, job: IngestJob) -> IngestResult | None:
        """Worker thread: parse, chunk, then embed one chunk at a time. Only
        `progress` is written to the session from here; the loop thread sets
        the result and status. Returns None if the session was deleted midway."""
        session = job.session
        page_chunks = parse_pdf(job.pdf_path)
        matrix = np.empty((len(page_chunks), EMBEDDING_DIM), dtype=np.float32)
        for index, chunk in enumerate(page_chunks):
            if session.cancelled:
                return None
            matrix[index] = self._embedder.encode_one(chunk.text)
            session.progress = (index + 1) / len(page_chunks)
        chunks = [
            ChunkMeta(chunk_id=i, text=c.text, page_start=c.page_start, page_end=c.page_end)
            for i, c in enumerate(page_chunks)
        ]
        return IngestResult(matrix=matrix, chunks=chunks)

    @staticmethod
    def _fail(session: Session, error: str) -> None:
        session.status = SessionStatus.FAILED
        session.progress = None
        session.error = error
