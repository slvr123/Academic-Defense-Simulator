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
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from backend import memory
from backend.embedder import EMBEDDING_DIM, Embedder
from backend.pdf_ingest import IngestFailure, parse_pdf
from backend.schemas import IngestErrorCode
from backend.sessions import ChunkMeta, Session, SessionStatus

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = "Something went wrong while processing this PDF. Try again, or try a different file."


@dataclass(frozen=True)
class IngestJob:
    session: Session
    pdf_path: Path
    page_count: int
    # Taken by the route once the upload is on disk and its pages counted.
    spooled: memory.PhaseMemory | None = None


@dataclass(frozen=True)
class IngestResult:
    matrix: np.ndarray
    chunks: list[ChunkMeta]
    parse_s: float
    embed_s: float


@dataclass(frozen=True)
class JobStats:
    """The last finished job, for GET /debug/memory. Same numbers as the `ingest` log line."""

    session_id: str
    outcome: str
    pages: int
    chunks: int | None
    job_peak_rss_mb: float
    parse_s: float | None
    embed_s: float | None
    total_s: float
    # RSS and VmHWM at each phase boundary, in order: spooled, parsed, released
    # (after the parse-to-embed gc + trim), embedded, job_end (after the final trim).
    # VmHWM only rises, so the phase where it jumps is the one that set the peak.
    phases: list[memory.PhaseMemory]


class QueueFullError(Exception):
    """`max_waiting` jobs are already waiting."""


class IngestQueue:
    def __init__(self, embedder: Embedder, max_waiting: int) -> None:
        self._embedder = embedder
        self._max_waiting = max_waiting
        self._queue: asyncio.Queue[IngestJob] = asyncio.Queue()
        self._waiting: list[str] = []
        self.last_job: JobStats | None = None

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
        if session.cancelled:
            job.pdf_path.unlink(missing_ok=True)
            return
        session.status = SessionStatus.EMBEDDING
        session.page_count = job.page_count
        session.progress = 0.0
        phases = [job.spooled] if job.spooled else []
        sampler = memory.PeakSampler().start()
        started = time.perf_counter()
        result: IngestResult | None = None
        try:
            result = await asyncio.to_thread(self._ingest, job, phases)
        except IngestFailure as exc:
            self._fail(session, str(exc), exc.code)
        except Exception:
            logger.exception("ingest failed for session %s", session.id)
            self._fail(session, UNEXPECTED_ERROR)
        finally:
            job.pdf_path.unlink(missing_ok=True)
            peak_mb = sampler.stop()

        if result is not None and not session.cancelled:
            session.matrix = result.matrix
            session.chunks = result.chunks
            session.progress = 1.0
            session.status = SessionStatus.READY
        result_chunks = len(result.chunks) if result else None
        timings = (round(result.parse_s, 2), round(result.embed_s, 2)) if result else (None, None)
        del result  # the session holds the matrix and chunks now; drop our references first
        phases.append(_release("job_end", session.id))
        outcome = "cancelled" if session.cancelled else session.status.value
        stats = JobStats(
            session_id=session.id,
            outcome=outcome,
            pages=job.page_count,
            chunks=result_chunks,
            job_peak_rss_mb=round(peak_mb, 1),
            parse_s=timings[0],
            embed_s=timings[1],
            total_s=round(time.perf_counter() - started, 2),
            phases=phases,
        )
        self.last_job = stats
        logger.info(
            "ingest %s session=%s pages=%d chunks=%s parse_s=%s embed_s=%s total_s=%.2f "
            "job_peak_rss=%.1fMB %s",
            stats.outcome,
            stats.session_id,
            stats.pages,
            "-" if stats.chunks is None else stats.chunks,
            "-" if stats.parse_s is None else f"{stats.parse_s:.2f}",
            "-" if stats.embed_s is None else f"{stats.embed_s:.2f}",
            stats.total_s,
            stats.job_peak_rss_mb,
            memory.describe(),
        )

    def _ingest(self, job: IngestJob, phases: list[memory.PhaseMemory]) -> IngestResult | None:
        """Worker thread: parse, chunk, then embed one chunk at a time. Only
        `progress` is written to the session from here; the loop thread sets
        the result and status. Returns None if the session was deleted midway."""
        session = job.session
        started = time.perf_counter()
        # parse_pdf closes the PDF before returning, and every per-page string it
        # built is local to it, so only the chunk texts survive into embedding.
        filtered = parse_pdf(job.pdf_path)
        page_chunks = filtered.chunks
        parsed = time.perf_counter()
        phases.append(memory.snapshot("parsed"))
        phases.append(_release("released", session.id))
        matrix = np.empty((len(page_chunks), EMBEDDING_DIM), dtype=np.float32)
        for index, chunk in enumerate(page_chunks):
            if session.cancelled:
                return None
            matrix[index] = self._embedder.encode_one(chunk.text)
            session.progress = (index + 1) / len(page_chunks)
        phases.append(memory.snapshot("embedded"))
        chunks = [
            ChunkMeta(chunk_id=i, text=c.text, page_start=c.page_start, page_end=c.page_end, attribution=a)
            for i, (c, a) in enumerate(zip(page_chunks, filtered.attributions))
        ]
        return IngestResult(
            matrix=matrix,
            chunks=chunks,
            parse_s=parsed - started,
            embed_s=time.perf_counter() - parsed,
        )

    @staticmethod
    def _fail(session: Session, error: str, code: IngestErrorCode | None = None) -> None:
        session.status = SessionStatus.FAILED
        session.progress = None
        session.error = error
        session.error_code = code


def _release(phase: str, session_id: str) -> memory.PhaseMemory:
    before, after, trimmed = memory.release_memory()
    logger.info(
        "memory release (%s) session=%s rss_before=%.1fMB rss_after=%.1fMB malloc_trim=%s",
        phase, session_id, before, after, "yes" if trimmed else "unavailable",
    )
    return memory.snapshot(phase)
