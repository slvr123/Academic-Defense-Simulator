"""Build the sample document's precomputed sidecar (v1.0.1 Decisions 2, 3, 4).

Ingests `academic_defense_simulator/sample/sample-capstone-anicheck.pdf` once
through the live pipeline and writes the three derived artifacts next to it:

    chunks.json      chunk texts, exactly as the live chunker produces them
    embeddings.npy   the chunk embedding matrix
    manifest.json    the staleness-guard payload + precomputed relevance result

Re-runnable by design: whenever the chunker's parameters or the embedding model
change, the guard in `sample_document.py` starts rejecting the committed sidecar
and this script is what regenerates it.

Makes exactly one LLM call — the v0.3g relevance gate, pinned to gemini-2.5-flash
(Decision 4), the same model the live gate uses. Pass --skip-assessment to
rebuild the vectors without spending it; the existing manifest's assessment is
carried forward.

Run: python scripts/build_sample_sidecar.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.document_relevance import assess_document
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.models.document_assessment import DocumentAssessment
from academic_defense_simulator.rag import chunking
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.sample_document import (
    CHUNKS_PATH,
    EMBEDDINGS_PATH,
    MANIFEST_PATH,
    SAMPLE_PDF_PATH,
    SampleManifest,
    sha256_of,
)

# Mirrors streamlit_app._RELEVANCE_ASSESSMENT_MODEL — the gate is a judgment task
# and flash-lite is on record as unfit for those. The precomputed assessment has
# to come from the same model the live gate would have used, or it is not the
# same result.
_RELEVANCE_ASSESSMENT_MODEL = "gemini-2.5-flash"
_LLM_STAGE_RELEVANCE_GATE = "relevance gate"  # matches streamlit_app.LLM_STAGE_RELEVANCE_GATE


def _count_pdf_pages(path: Path) -> int:
    import fitz  # PyMuPDF

    document = fitz.open(str(path))
    try:
        return document.page_count
    finally:
        document.close()


def _carry_forward_assessment() -> DocumentAssessment:
    if not MANIFEST_PATH.exists():
        raise SystemExit(
            "--skip-assessment needs an existing manifest.json to carry the "
            "assessment forward from, and none exists yet."
        )
    existing = SampleManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
    return existing.document_assessment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the sample document sidecar.")
    parser.add_argument(
        "--skip-assessment",
        action="store_true",
        help="Reuse the existing manifest's relevance assessment instead of making the call.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    print(f"Sample PDF: {SAMPLE_PDF_PATH}")
    sha = sha256_of(SAMPLE_PDF_PATH)
    page_count = _count_pdf_pages(SAMPLE_PDF_PATH)
    print(f"  sha256:     {sha}")
    print(f"  pages:      {page_count}")

    print(f"\nChunking (target={chunking.TARGET_CHARS}, overlap={chunking.OVERLAP_CHARS})...")
    texts = chunking.chunk_pdf(SAMPLE_PDF_PATH)
    print(f"  chunks:     {len(texts)}")
    print(f"  chars:      {[len(t) for t in texts]}")

    print("\nEmbedding...")
    embedding_model = EmbeddingModel()
    embeddings = np.array(embedding_model.encode(texts), dtype=np.float32)
    print(f"  model:      {embedding_model.model_name}")
    print(f"  matrix:     {embeddings.shape} {embeddings.dtype}")

    if args.skip_assessment:
        assessment = _carry_forward_assessment()
        print("\nRelevance gate: skipped, carried forward from the existing manifest.")
    else:
        print(f"\nRelevance gate ({_RELEVANCE_ASSESSMENT_MODEL})...")
        provider = GeminiProvider(
            api_key=load_settings().gemini_api_key.get(),
            model=_RELEVANCE_ASSESSMENT_MODEL,
            label=_LLM_STAGE_RELEVANCE_GATE,
        )
        assessment = assess_document(texts, provider)
    print(f"  {assessment.model_dump_json(indent=2)}")

    manifest = SampleManifest(
        embedding_model=embedding_model.model_name,
        embedding_dim=int(embeddings.shape[1]),
        chunker_target_chars=chunking.TARGET_CHARS,
        chunker_overlap_chars=chunking.OVERLAP_CHARS,
        chunk_count=len(texts),
        pdf_sha256=sha,
        page_count=page_count,
        document_assessment=assessment,
    )

    CHUNKS_PATH.write_text(json.dumps(texts, indent=2, ensure_ascii=False), encoding="utf-8")
    np.save(EMBEDDINGS_PATH, embeddings)
    MANIFEST_PATH.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")

    print("\nWrote:")
    for path in (CHUNKS_PATH, EMBEDDINGS_PATH, MANIFEST_PATH):
        print(f"  {path}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
