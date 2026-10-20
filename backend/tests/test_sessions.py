"""Session store (cap, TTL, delete), without the app or the model."""

from __future__ import annotations

import numpy as np
import pytest

from backend.schemas import PanelConfig
from backend.sessions import ChunkMeta, SessionCapacityError, SessionStatus, SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _store(max_live: int = 5, ttl: float = 1800) -> tuple[SessionStore, FakeClock]:
    clock = FakeClock()
    return SessionStore(max_live=max_live, ttl_seconds=ttl, clock=clock), clock


def _ready(store: SessionStore, rows: list[list[float]]):
    session = store.create(PanelConfig(), "doc.pdf")
    matrix = np.array(rows, dtype=np.float32)
    session.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    session.chunks = [ChunkMeta(i, f"chunk {i}", i, i) for i in range(len(rows))]
    session.status = SessionStatus.READY
    return session


# --- store ---


def test_unknown_id_is_none():
    store, _ = _store()
    assert store.get("not-a-session") is None


def test_live_cap_raises_and_failed_sessions_free_a_slot():
    store, _ = _store(max_live=2)
    first = store.create(PanelConfig(), "a.pdf")
    store.create(PanelConfig(), "b.pdf")
    with pytest.raises(SessionCapacityError):
        store.create(PanelConfig(), "c.pdf")

    first.status = SessionStatus.FAILED
    store.create(PanelConfig(), "c.pdf")  # a failed session doesn't hold a live slot


def test_ready_and_failed_sessions_expire_after_idle_ttl():
    store, clock = _store(ttl=60)
    ready = _ready(store, [[1, 0]])
    failed = store.create(PanelConfig(), "bad.pdf")
    failed.status = SessionStatus.FAILED

    clock.now += 59
    assert store.get(ready.id) is ready  # touching it resets its idle clock
    clock.now += 59
    assert store.sweep() == [failed.id]
    clock.now += 2
    assert store.get(ready.id) is None  # lazy eviction on access, before any sweep
    assert len(store) == 0


def test_queued_and_embedding_sessions_never_expire():
    store, clock = _store(ttl=60)
    queued = store.create(PanelConfig(), "q.pdf")
    embedding = store.create(PanelConfig(), "e.pdf")
    embedding.status = SessionStatus.EMBEDDING

    clock.now += 10_000
    assert store.sweep() == []
    assert store.get(queued.id) is queued
    assert store.get(embedding.id) is embedding


def test_expired_sessions_release_capacity():
    store, clock = _store(max_live=1, ttl=60)
    _ready(store, [[1, 0]])
    assert not store.has_capacity()
    clock.now += 61
    assert store.has_capacity()


def test_delete_frees_the_session_and_flags_it_cancelled():
    store, _ = _store()
    session = _ready(store, [[1, 0]])
    assert store.delete(session.id) is session
    assert session.cancelled and session.matrix is None
    assert store.get(session.id) is None
    assert store.delete(session.id) is None
