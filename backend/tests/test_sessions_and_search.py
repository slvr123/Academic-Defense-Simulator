"""Session store (cap, TTL, delete) and top-k search, without the app or the model."""

from __future__ import annotations

import numpy as np
import pytest

from backend.retrieval import search
from backend.schemas import PanelConfig
from backend.sessions import ChunkMeta, SessionCapacityError, SessionStatus, SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class MappedEmbedder:
    """Returns a fixed vector per known query."""

    def __init__(self, vectors: dict[str, np.ndarray]) -> None:
        self._vectors = vectors

    def encode_one(self, text: str) -> np.ndarray:
        return self._vectors[text]


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


# --- search ---


def test_search_returns_top_k_best_first():
    store, _ = _store()
    session = _ready(store, [[1, 0, 0], [0, 1, 0], [0.8, 0.6, 0], [0, 0, 1]])
    embedder = MappedEmbedder({"q": np.array([1.0, 0.1, 0.0], dtype=np.float32)})

    hits = search(session, "q", 2, embedder=embedder)

    assert [h.chunk.chunk_id for h in hits] == [0, 2]
    assert hits[0].score > hits[1].score
    assert hits[0].score == pytest.approx(1 / np.sqrt(1.01), rel=1e-5)  # query gets normalised


def test_search_k_larger_than_the_document_returns_everything():
    store, _ = _store()
    session = _ready(store, [[1, 0], [0, 1]])
    embedder = MappedEmbedder({"q": np.array([0.0, 1.0], dtype=np.float32)})
    assert [h.chunk.chunk_id for h in search(session, "q", 10, embedder=embedder)] == [1, 0]


def test_search_rejects_a_session_that_is_not_ready():
    store, _ = _store()
    session = store.create(PanelConfig(), "q.pdf")
    with pytest.raises(ValueError, match="not ready"):
        search(session, "q", 1, embedder=MappedEmbedder({}))
