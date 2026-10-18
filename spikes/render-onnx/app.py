"""Throwaway hosting spike: all-MiniLM-L6-v2 on ONNX Runtime behind FastAPI.

Not part of the app. Spike 2 asks whether batched embedding, a capped glibc
arena count and a one-at-a-time lock keep the model under Render free's memory
limit once the real backend's heavy imports are loaded. Spike 1's code is at
commit 11ba5fc.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import psutil
from fastapi import FastAPI

HERE = Path(__file__).parent
MODEL_DIR = HERE / "model"
# sentence-transformers' max_seq_length for this model. tokenizer.json ships
# with truncation at 128, so it must be overridden or long chunks diverge.
MAX_SEQ_LENGTH = 256
MB = 1024 * 1024

# One embedding at a time per process, so concurrent requests queue instead of
# stacking activation memory.
EMBED_LOCK = threading.Lock()


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / MB


def os_peak_rss_mb() -> float | None:
    """The kernel's high-water mark for this process, independent of sampling."""
    if sys.platform.startswith("linux"):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    info = psutil.Process().memory_info()
    peak = getattr(info, "peak_wset", None)
    return peak / MB if peak is not None else None


class PeakSampler:
    """Polls RSS on a background thread while a block runs."""

    def __init__(self, interval_s: float = 0.005) -> None:
        self._interval_s = interval_s
        self._stop = threading.Event()
        self.peak_mb = 0.0

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_mb = max(self.peak_mb, rss_mb())
            time.sleep(self._interval_s)

    def __enter__(self) -> PeakSampler:
        self.peak_mb = rss_mb()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak_mb = max(self.peak_mb, rss_mb())


@dataclass
class Embedder:
    session: Any
    tokenizer: Any
    input_names: list[str]
    batch_size: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Mirror SentenceTransformer.encode(normalize_embeddings=True):
        truncate at 256 tokens, mean-pool over the attention mask, L2-normalise.
        Runs in batches of batch_size, holding EMBED_LOCK for the whole call."""
        with EMBED_LOCK:
            batches = [
                self._encode_batch(texts[start : start + self.batch_size])
                for start in range(0, len(texts), self.batch_size)
            ]
        return np.concatenate(batches)

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        token_vectors = self.session.run(None, {n: feeds[n] for n in self.input_names})[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (token_vectors * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)


def import_backend_deps() -> None:
    """Load the real backend's heavy runtime modules so the baseline RSS is
    honest. Import only: no client is constructed and no API call is made."""
    import fitz  # noqa: F401  (pymupdf)
    import google.genai  # noqa: F401
    import pydantic  # noqa: F401


def load_embedder() -> Embedder:
    # Imported here so the "before load" RSS reading excludes the runtime itself.
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("ORT_THREADS", "1"))
    # The CPU arena keeps freed activation buffers for reuse, trading RSS for speed.
    options.enable_cpu_mem_arena = os.environ.get("ORT_ARENA", "0") == "1"
    session = ort.InferenceSession(
        str(MODEL_DIR / "model.onnx"), options, providers=["CPUExecutionProvider"]
    )
    return Embedder(
        session,
        tokenizer,
        [i.name for i in session.get_inputs()],
        batch_size=int(os.environ.get("EMBED_BATCH", "2")),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    rss_bare = rss_mb()
    import_backend_deps()
    rss_before = rss_mb()
    started = time.perf_counter()
    app.state.embedder = load_embedder()
    app.state.load = {
        "model_load_s": round(time.perf_counter() - started, 3),
        "rss_bare_mb": round(rss_bare, 1),
        "rss_before_load_mb": round(rss_before, 1),
        "rss_after_load_mb": round(rss_mb(), 1),
    }
    app.state.chunks = json.loads((HERE / "chunks.json").read_text(encoding="utf-8"))
    app.state.reference = np.load(HERE / "embeddings.npy")
    app.state.requests = 0
    yield


app = FastAPI(lifespan=lifespan)


def cosines(vectors: np.ndarray, reference: np.ndarray) -> np.ndarray:
    return (vectors * reference).sum(axis=1) / (
        np.linalg.norm(vectors, axis=1) * np.linalg.norm(reference, axis=1)
    )


def run_info() -> dict[str, Any]:
    session_options = app.state.embedder.session.get_session_options()
    os_peak = os_peak_rss_mb()
    return {
        "rss_os_high_water_mb": round(os_peak, 1) if os_peak is not None else None,
        **app.state.load,
        "embed_batch": app.state.embedder.batch_size,
        "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX"),
        "ort_threads": session_options.intra_op_num_threads,
        "ort_cpu_mem_arena": session_options.enable_cpu_mem_arena,
        "cpu_count_visible": os.cpu_count(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/spike")
def spike() -> dict[str, Any]:
    app.state.requests += 1
    with PeakSampler() as sampler:
        started = time.perf_counter()
        vectors = app.state.embedder.encode(app.state.chunks)
        embed_s = time.perf_counter() - started
    similarity = cosines(vectors, app.state.reference)
    return {
        "request_number": app.state.requests,
        "chunk_count": len(app.state.chunks),
        "embed_s": round(embed_s, 3),
        "rss_peak_during_embed_mb": round(sampler.peak_mb, 1),
        "cosine_min": float(similarity.min()),
        "cosine_mean": float(similarity.mean()),
        "cosine_per_chunk": [round(float(c), 7) for c in similarity],
        **run_info(),
    }


@app.get("/spike-concurrent")
def spike_concurrent() -> dict[str, Any]:
    """Two full embeddings on two threads at once; the lock should serialise them."""
    app.state.requests += 1
    results: list[dict[str, Any]] = [{}, {}]

    def worker(index: int) -> None:
        started = time.perf_counter()
        try:
            vectors = app.state.embedder.encode(app.state.chunks)
            results[index]["cosine_min"] = float(cosines(vectors, app.state.reference).min())
        except Exception as exc:  # reported, not raised: the point is to see it
            results[index]["error"] = repr(exc)
        results[index]["elapsed_s"] = round(time.perf_counter() - started, 3)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    with PeakSampler() as sampler:
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wall_s = time.perf_counter() - started
    return {
        "request_number": app.state.requests,
        "chunk_count": len(app.state.chunks),
        "wall_s": round(wall_s, 3),
        "threads": results,
        "rss_peak_during_embed_mb": round(sampler.peak_mb, 1),
        **run_info(),
    }
