"""Sample sidecar tests (v1.0.1 Decisions 2/3/4).

The guard is the point of this file. Every field `SampleManifest` records is
mismatched independently and the fallback is observed firing — a guard whose
branches were never taken is an assertion about code that has never run.

No real `sentence-transformers` model is loaded: the sidecar path never encodes
anything (that is the whole saving), and the live-ingestion fallback only needs
something with `.model_name` and `.encode`, so a stub stands in. `chunk_pdf` does
run against the real committed sample PDF on the fallback paths, which is the
point — the fallback has to actually produce a working document.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from academic_defense_simulator import sample_document as sd
from academic_defense_simulator.rag import chunking

_LIVE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384


class _StubEncoder:
    """Duck-types EmbeddingModel without loading 90MB of weights."""

    def __init__(self, model_name: str = _LIVE_MODEL) -> None:
        self.model_name = model_name
        self.encode_calls = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls += 1
        return [[0.0] * _EMBEDDING_DIM for _ in texts]


@pytest.fixture
def real_manifest() -> dict:
    return json.loads(sd.MANIFEST_PATH.read_text(encoding="utf-8"))


def _tamper(tmp_path, monkeypatch, manifest: dict, field: str, value) -> None:
    """Point the loader at a manifest with exactly one field altered. The real
    committed manifest is never written to."""
    manifest = dict(manifest)
    manifest[field] = value
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(sd, "MANIFEST_PATH", path)


# --- the sidecar loads and matches what the manifest promises -----------------


def test_sidecar_loads_and_matches_manifest(real_manifest):
    encoder = _StubEncoder()
    doc = sd.load_sample_document(encoder)

    assert doc.source == "sidecar"
    assert len(doc.chunks) == real_manifest["chunk_count"]
    assert all(len(c.embedding) == real_manifest["embedding_dim"] for c in doc.chunks)
    assert doc.page_count == real_manifest["page_count"]
    # The saving is real: nothing was encoded on this path.
    assert encoder.encode_calls == 0


def test_sidecar_carries_the_precomputed_assessment(real_manifest):
    doc = sd.load_sample_document(_StubEncoder())

    assert doc.assessment is not None
    assert doc.assessment.is_defense_material is True
    assert doc.assessment.model_dump() == real_manifest["document_assessment"]


def test_sidecar_chunk_texts_match_chunks_json():
    doc = sd.load_sample_document(_StubEncoder())
    expected = json.loads(sd.CHUNKS_PATH.read_text(encoding="utf-8"))

    assert [c.text for c in doc.chunks] == expected


# --- every guarded field, mismatched independently ----------------------------


@pytest.mark.parametrize(
    "field, bad_value, expected_in_log",
    [
        ("embedding_model", "sentence-transformers/all-mpnet-base-v2", "embedding_model"),
        ("embedding_dim", 768, "embedding_dim"),
        ("chunker_target_chars", chunking.TARGET_CHARS + 200, "chunker_target_chars"),
        ("chunker_overlap_chars", chunking.OVERLAP_CHARS + 100, "chunker_overlap_chars"),
        ("chunk_count", 99, "chunk_count"),
        ("pdf_sha256", "0" * 64, "pdf_sha256"),
    ],
)
def test_guard_fires_per_field(
    tmp_path, monkeypatch, caplog, real_manifest, field, bad_value, expected_in_log
):
    _tamper(tmp_path, monkeypatch, real_manifest, field, bad_value)

    encoder = _StubEncoder()
    with caplog.at_level(logging.WARNING, logger=sd.__name__):
        doc = sd.load_sample_document(encoder)

    assert doc.source == "live", f"{field} mismatch did not discard the sidecar"
    assert doc.assessment is None, "a rejected sidecar must not carry its assessment forward"
    assert encoder.encode_calls == 1, "fallback did not re-embed"
    assert len(doc.chunks) > 0

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(expected_in_log in m for m in warnings), warnings
    assert any("stale" in m for m in warnings), warnings


def test_guard_fires_when_embedding_rows_disagree_with_manifest(tmp_path, monkeypatch, caplog):
    """The one field with two independent sources of truth: `chunk_count` is
    checked against chunks.json *and* against the embedding matrix's row count,
    so a half-regenerated sidecar (texts rewritten, vectors not) is caught."""
    short_matrix = tmp_path / "embeddings.npy"
    np.save(short_matrix, np.zeros((13, _EMBEDDING_DIM), dtype=np.float32))
    monkeypatch.setattr(sd, "EMBEDDINGS_PATH", short_matrix)

    encoder = _StubEncoder()
    with caplog.at_level(logging.WARNING, logger=sd.__name__):
        doc = sd.load_sample_document(encoder)

    assert doc.source == "live"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("embeddings.npy rows" in m for m in warnings), warnings


def test_guard_fires_when_the_live_encoder_is_a_different_model(caplog):
    """The mismatch that motivates the whole guard: vectors computed by one model
    being served against queries embedded by another."""
    with caplog.at_level(logging.WARNING, logger=sd.__name__):
        doc = sd.load_sample_document(_StubEncoder(model_name="some-other/encoder"))

    assert doc.source == "live"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("embedding_model" in m for m in warnings), warnings


def test_missing_sidecar_falls_back_rather_than_raising(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sd, "MANIFEST_PATH", tmp_path / "does-not-exist.json")

    with caplog.at_level(logging.WARNING, logger=sd.__name__):
        doc = sd.load_sample_document(_StubEncoder())

    assert doc.source == "live"
    assert any("unreadable" in r.getMessage() for r in caplog.records)


def test_corrupt_manifest_falls_back_rather_than_raising(tmp_path, monkeypatch, caplog):
    bad = tmp_path / "manifest.json"
    bad.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(sd, "MANIFEST_PATH", bad)

    with caplog.at_level(logging.WARNING, logger=sd.__name__):
        doc = sd.load_sample_document(_StubEncoder())

    assert doc.source == "live"
    assert any("unreadable" in r.getMessage() for r in caplog.records)


# --- no LLM call on either path -----------------------------------------------


@pytest.mark.parametrize("force_fallback", [False, True])
def test_loader_makes_no_llm_call(tmp_path, monkeypatch, real_manifest, force_fallback):
    """Decision 3's fallback must not cost an API call, and the sidecar path must
    not make one either. Forced rather than asserted: any provider construction
    raises, so a hidden call site fails the test loudly."""
    from academic_defense_simulator.llm import gemini_provider

    def _explode(*args, **kwargs):
        raise AssertionError("the sample loader must not construct an LLM provider")

    monkeypatch.setattr(gemini_provider.GeminiProvider, "__init__", _explode)

    if force_fallback:
        _tamper(tmp_path, monkeypatch, real_manifest, "pdf_sha256", "0" * 64)

    doc = sd.load_sample_document(_StubEncoder())

    assert doc.source == ("live" if force_fallback else "sidecar")
