"""In-memory sessions keyed by uuid4, with an idle TTL and a cap on live sessions.

Everything here runs on the event loop thread except the worker's progress and
result writes, which are plain attribute assignments on a session it owns.
Losing every session on restart is accepted (M1 brief).

Eviction is both periodic and lazy. The app's lifespan calls `sweep()` every
`sweep_interval_seconds`, because memory is the binding constraint on the free
instance and an abandoned session's matrix should not wait for someone to touch
the store before it's freed. `get()` also treats an expired session as gone, so
a session past its TTL is never served between sweeps.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from backend.schemas import PanelConfig


class SessionStatus(str, Enum):
    QUEUED = "queued"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


# Only finished sessions age out; a queued or embedding one belongs to the worker.
_EXPIRABLE = frozenset({SessionStatus.READY, SessionStatus.FAILED})


@dataclass(frozen=True)
class ChunkMeta:
    """Row metadata parallel to the session matrix. Pages are 0-based PyMuPDF indices."""

    chunk_id: int
    text: str
    page_start: int
    page_end: int


@dataclass
class Session:
    id: str
    panel: PanelConfig
    filename: str
    last_access: float
    status: SessionStatus = SessionStatus.QUEUED
    progress: float | None = None
    page_count: int | None = None
    error: str | None = None
    matrix: np.ndarray | None = None  # (chunk_count, 384) float32, unit rows
    chunks: list[ChunkMeta] = field(default_factory=list)
    cancelled: bool = False  # set by DELETE; the worker drops the job after its current chunk

    @property
    def chunk_count(self) -> int | None:
        return len(self.chunks) if self.status is SessionStatus.READY else None


class SessionCapacityError(Exception):
    """The live-session cap is reached."""


class SessionStore:
    def __init__(
        self,
        max_live: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_live = max_live
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def live_count(self) -> int:
        # Failed sessions hold no matrix, so they don't take a live slot.
        return sum(1 for s in self._sessions.values() if s.status is not SessionStatus.FAILED)

    def has_capacity(self) -> bool:
        self.sweep()
        return self.live_count() < self._max_live

    def create(self, panel: PanelConfig, filename: str) -> Session:
        if not self.has_capacity():
            raise SessionCapacityError
        session = Session(
            id=str(uuid.uuid4()), panel=panel, filename=filename, last_access=self._clock()
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._is_expired(session, self._clock()):
            del self._sessions[session_id]
            return None
        session.last_access = self._clock()
        return session

    def delete(self, session_id: str) -> Session | None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.cancelled = True
            session.matrix = None
            session.chunks = []
        return session

    def sweep(self) -> list[str]:
        now = self._clock()
        expired = [sid for sid, s in self._sessions.items() if self._is_expired(s, now)]
        for session_id in expired:
            del self._sessions[session_id]
        return expired

    def __len__(self) -> int:
        return len(self._sessions)

    def _is_expired(self, session: Session, now: float) -> bool:
        return session.status in _EXPIRABLE and now - session.last_access > self._ttl_seconds
