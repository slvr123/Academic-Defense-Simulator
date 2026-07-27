"""Sample-document sidecar: precomputed chunks + embeddings, with a staleness
guard that fails open to live ingestion (v1.0.1 Decisions 2, 3, 4).

Business logic only — no `streamlit` import (standing import boundary). The
caller supplies the `EmbeddingModel`, the same shape `document_relevance.
assess_document` uses for its provider: this module never constructs the
expensive dependency it needs, so the Streamlit layer's cached model is reused
rather than a second copy being loaded.

What the sidecar saves, stated honestly (Decision 2): the chunking pass and the
document-embedding pass. It does *not* save the sentence-transformers model
load — every turn embeds a freshly-generated query, so the encoder is live
either way.

Why the guard exists (Decision 3): precomputed vectors are silently coupled to
the embedding model and to the chunker's parameters. If either changes, cosine
similarity keeps returning confident-looking numbers against stale vectors and
retrieval quality degrades with no error anywhere. Every field the sidecar
depends on is therefore asserted against live configuration on load, and any
mismatch discards the sidecar and re-ingests rather than serving quiet garbage.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ValidationError

from academic_defense_simulator.models.document_assessment import DocumentAssessment
from academic_defense_simulator.rag import chunking
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

logger = logging.getLogger(__name__)

SAMPLE_DIR = Path(__file__).parent / "sample"
SAMPLE_PDF_PATH = SAMPLE_DIR / "sample-capstone-anicheck.pdf"
CHUNKS_PATH = SAMPLE_DIR / "chunks.json"
EMBEDDINGS_PATH = SAMPLE_DIR / "embeddings.npy"
MANIFEST_PATH = SAMPLE_DIR / "manifest.json"

# Shown wherever a real upload would show its filename (case-file sidebar, the
# loaded-document summary card).
SAMPLE_DISPLAY_NAME = "sample-capstone-anicheck.pdf"


class SampleManifest(BaseModel):
    """The guard payload (Decision 3). Every field here is asserted on load; a
    field that is recorded but never asserted would be decoration, not a guard.

    `document_assessment` is the precomputed v0.3g relevance result (Decision 4)
    — a real `DocumentAssessment` from a real gemini-2.5-flash call, not a
    hand-written stand-in."""

    embedding_model: str
    embedding_dim: int
    chunker_target_chars: int
    chunker_overlap_chars: int
    chunk_count: int
    pdf_sha256: str
    page_count: int
    document_assessment: DocumentAssessment


@dataclass(frozen=True)
class SampleDocument:
    """`source` is load-path provenance, surfaced in dev-view (Decision 4's
    transparency requirement): "sidecar" means the precomputed assessment below
    is real and reused, "live" means the guard rejected the sidecar and the
    caller must run the relevance gate itself — hence `assessment` is None
    there, never a stale value carried across a failed guard."""

    chunks: list[Chunk]
    page_count: int
    assessment: DocumentAssessment | None
    source: str  # "sidecar" | "live"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _count_pdf_pages(path: Path) -> int:
    import fitz  # PyMuPDF

    document = fitz.open(str(path))
    try:
        return document.page_count
    finally:
        document.close()


def _find_mismatch(
    manifest: SampleManifest,
    chunk_texts: list[str],
    embeddings: np.ndarray,
    live_model: str,
) -> str | None:
    """First stale field, or None if the sidecar is usable. Returns a name rather
    than a bool so the warning can say *which* field moved — a guard that only
    reports "stale" leaves the next person bisecting the manifest by hand."""
    if manifest.embedding_model != live_model:
        return f"embedding_model (manifest {manifest.embedding_model!r}, live {live_model!r})"

    if embeddings.ndim != 2:
        return f"embeddings array rank (expected 2 dimensions, got {embeddings.ndim})"

    if manifest.embedding_dim != embeddings.shape[1]:
        return (
            f"embedding_dim (manifest {manifest.embedding_dim}, "
            f"embeddings.npy {embeddings.shape[1]})"
        )

    if manifest.chunker_target_chars != chunking.TARGET_CHARS:
        return (
            f"chunker_target_chars (manifest {manifest.chunker_target_chars}, "
            f"live {chunking.TARGET_CHARS})"
        )

    if manifest.chunker_overlap_chars != chunking.OVERLAP_CHARS:
        return (
            f"chunker_overlap_chars (manifest {manifest.chunker_overlap_chars}, "
            f"live {chunking.OVERLAP_CHARS})"
        )

    if manifest.chunk_count != len(chunk_texts):
        return (
            f"chunk_count (manifest {manifest.chunk_count}, "
            f"chunks.json {len(chunk_texts)})"
        )

    if manifest.chunk_count != embeddings.shape[0]:
        return (
            f"chunk_count (manifest {manifest.chunk_count}, "
            f"embeddings.npy rows {embeddings.shape[0]})"
        )

    live_sha = sha256_of(SAMPLE_PDF_PATH)
    if manifest.pdf_sha256 != live_sha:
        return f"pdf_sha256 (manifest {manifest.pdf_sha256}, file {live_sha})"

    return None


def _load_sidecar(embedding_model: EmbeddingModel) -> SampleDocument | None:
    """None means "not usable, fall back" for every reason — missing files, a
    manifest that no longer parses, or a guard mismatch. All three are the same
    situation from the caller's side: the precomputed copy cannot be trusted."""
    try:
        manifest = SampleManifest.model_validate_json(
            MANIFEST_PATH.read_text(encoding="utf-8")
        )
        chunk_texts: list[str] = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        embeddings = np.load(EMBEDDINGS_PATH)
    except (OSError, ValueError, ValidationError) as exc:
        # ValueError covers json.JSONDecodeError and numpy's own malformed-file
        # errors; ValidationError covers a manifest whose shape has drifted.
        logger.warning(
            "Sample sidecar unreadable, falling back to live ingestion: %s", exc
        )
        return None

    # The live model is the instance the caller actually holds, not the class
    # default — if the caller ever pins a different encoder, that is the
    # configuration these vectors have to match.
    mismatch = _find_mismatch(manifest, chunk_texts, embeddings, embedding_model.model_name)
    if mismatch is not None:
        # Deliberately loud (Decision 3, "not negotiable"): a silent fallback
        # would hide exactly the regression this guard exists to catch.
        logger.warning(
            "Sample sidecar is stale — discarding it and ingesting the sample PDF "
            "live. Mismatched field: %s. Regenerate with "
            "scripts/build_sample_sidecar.py.",
            mismatch,
        )
        return None

    chunks = [
        Chunk(text=text, embedding=vector.tolist())
        for text, vector in zip(chunk_texts, embeddings)
    ]
    return SampleDocument(
        chunks=chunks,
        page_count=manifest.page_count,
        assessment=manifest.document_assessment,
        source="sidecar",
    )


def _ingest_live(embedding_model: EmbeddingModel) -> SampleDocument:
    """The fallback path: exactly what a real upload does, minus the relevance
    gate — that stays the caller's call, because the caller owns the provider."""
    texts = chunking.chunk_pdf(SAMPLE_PDF_PATH)
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    return SampleDocument(
        chunks=chunks,
        page_count=_count_pdf_pages(SAMPLE_PDF_PATH),
        assessment=None,
        source="live",
    )


def load_sample_document(embedding_model: EmbeddingModel) -> SampleDocument:
    """Precomputed sidecar if it is current, freshly-ingested if it is not.

    Never raises on a stale or damaged sidecar — the user gets a working session
    either way and just pays the ingestion cost, the same fail-open shape the
    relevance gate and the answer-suggestions call already use."""
    sidecar = _load_sidecar(embedding_model)
    if sidecar is not None:
        return sidecar
    return _ingest_live(embedding_model)
