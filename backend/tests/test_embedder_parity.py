"""ONNX embedder vs sentence-transformers on real chunk text (M1 brief, item 3).

Skipped unless sentence-transformers is installed (dev only) and the model files
have been fetched (`python -m backend.fetch_model`).

Inputs: the committed sample's 14 chunks (long, so they exercise truncation at
256 tokens) plus the first paragraph of six of them (short, so no truncation):
20 texts, all real document text.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from academic_defense_simulator.rag.chunking import chunk_pdf_with_pages
from backend.embedder import EMBEDDING_DIM, MAX_SEQ_LENGTH, OnnxEmbedder
from backend.settings import Settings

sentence_transformers = pytest.importorskip("sentence_transformers")

SAMPLE_PDF = (
    Path(__file__).resolve().parents[2]
    / "academic_defense_simulator"
    / "sample"
    / "sample-capstone-anicheck.pdf"
)
MODEL_DIR = Settings().model_dir

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "model.onnx").exists(),
    reason="model not fetched; run `python -m backend.fetch_model`",
)


def _parity_texts() -> list[str]:
    chunks = [c.text for c in chunk_pdf_with_pages(SAMPLE_PDF)]
    first_paragraphs = [chunk.split("\n\n")[0] for chunk in chunks[:6]]
    return chunks + first_paragraphs


def test_onnx_matches_sentence_transformers_on_every_text():
    texts = _parity_texts()
    assert len(texts) == 20

    reference_model = sentence_transformers.SentenceTransformer(
        "sentence-transformers/all-MiniLM-L6-v2", device="cpu"
    )
    assert reference_model.max_seq_length == MAX_SEQ_LENGTH
    reference = reference_model.encode(texts, normalize_embeddings=True)

    embedder = OnnxEmbedder.load(MODEL_DIR)
    onnx = np.stack([embedder.encode_one(text) for text in texts])

    assert onnx.shape == (20, EMBEDDING_DIM)
    assert onnx.dtype == np.float32
    cosines = (onnx * reference).sum(axis=1)  # both sides are unit length
    assert cosines.min() >= 0.999, f"per-text cosines: {np.round(cosines, 6).tolist()}"
