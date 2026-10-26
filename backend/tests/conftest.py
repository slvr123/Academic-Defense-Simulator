"""Shared fixtures: synthetic PDFs, a controllable fake embedder, an in-memory
quota store, a settable clock, an app client."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.embedder import EMBEDDING_DIM
from backend.quota import InMemoryQuotaStore
from backend.tests.fake_gemini import FakeGemini
from backend.tests.fake_mimo import FakeMimo
from backend.settings import Settings

PARAGRAPH = (
    "The proponents evaluated the system with thirty respondents using an instrument "
    "adapted from the ISO/IEC 25010 software quality model, covering functional "
    "suitability, usability, reliability and portability."
)


def make_pdf(path: Path, pages: int = 3, paragraphs_per_page: int = 6, text: bool = True) -> Path:
    document = fitz.open()
    for page_number in range(pages):
        page = document.new_page()
        if text:
            body = "\n\n".join(f"Page {page_number} paragraph {i}. {PARAGRAPH}" for i in range(paragraphs_per_page))
            page.insert_textbox(fitz.Rect(36, 36, 576, 756), body, fontsize=8)
    document.save(path)
    return path


class FakeEmbedder:
    """Deterministic unit vectors. `hold()` makes every encode wait until
    `release()`; `step()` lets exactly one encode through."""

    def __init__(self) -> None:
        self._open = threading.Event()
        self._open.set()
        self._steps = threading.Semaphore(0)
        self._stepping = False
        self.calls = 0

    def hold(self) -> None:
        self._open.clear()

    def release(self) -> None:
        self._stepping = False
        self._open.set()
        self._steps.release(1000)

    def stepwise(self) -> None:
        self._stepping = True

    def step(self) -> None:
        self._steps.release()

    def encode_one(self, text: str) -> np.ndarray:
        if self._stepping:
            self._steps.acquire()
        self._open.wait()
        self.calls += 1
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
        vector = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM).astype(np.float32)
        return vector / np.linalg.norm(vector)


@pytest.fixture
def embedder() -> Iterator[FakeEmbedder]:
    fake = FakeEmbedder()
    yield fake
    fake.release()  # never leave a worker thread blocked at teardown


class FakeClock:
    """A settable UTC now. Starts at noon Pacific (19:00 UTC, PDT) on 2026-10-20."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def quota_store() -> InMemoryQuotaStore:
    return InMemoryQuotaStore()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def gemini() -> FakeGemini:
    return FakeGemini()


@pytest.fixture
def mimo() -> Iterator[FakeMimo]:
    fake = FakeMimo()
    yield fake
    if fake.block is not None:
        fake.block.set()  # never leave a /tts thread waiting at teardown


def make_settings(**overrides: object) -> Settings:
    """Settings that never read backend/.env, so tests can't reach a real database."""
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def client_factory(
    tmp_path: Path, embedder: FakeEmbedder, quota_store: InMemoryQuotaStore, clock: FakeClock, gemini: FakeGemini,
    mimo: FakeMimo,
) -> Callable[..., object]:
    @contextmanager
    def factory(**overrides: object) -> Iterator[TestClient]:
        settings = make_settings(upload_dir=tmp_path / "uploads", **overrides)
        app = create_app(
            settings, embedder_factory=lambda _settings: embedder,
            quota_store_factory=lambda _settings: quota_store, clock=clock,
            providers_factory=lambda _settings: gemini.providers, synthesize=mimo,
        )
        with TestClient(app) as client:
            try:
                yield client
            finally:
                embedder.release()

    return factory


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "thesis.pdf")


def upload(client: TestClient, path: Path, panel: str | None = None):
    data = {"panel": panel} if panel is not None else {}
    with path.open("rb") as handle:
        return client.post("/sessions", files={"file": (path.name, handle, "application/pdf")}, data=data)


def wait_for(client: TestClient, session_id: str, condition: Callable[[dict], bool], timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/sessions/{session_id}")
        body = response.json() if response.status_code == 200 else {"http": response.status_code}
        if condition(body):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; last state {body}")
        time.sleep(0.01)
