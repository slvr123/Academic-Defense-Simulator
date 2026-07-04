"""Standalone RAG pipeline inspector — chunk, embed, retrieve, preview prompt.

No Gemini call. Run: python scripts/inspect_rag.py <pdf_path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PANELIST_SYSTEM_PROMPT
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

_ACTIVE_ARCHETYPE = "methodology_expert"
_PANELIST_NAME = "Reyes"
_DIFFICULTY = 2
_PREVIEW_CHARS = 120


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the RAG pipeline on a PDF, no LLM call.")
    parser.add_argument("pdf_path", help="Path to the PDF to inspect")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    print(f"Chunking: {args.pdf_path}\n")
    texts = chunk_pdf(args.pdf_path)
    if not texts:
        print("No chunks extracted — nothing to inspect.")
        sys.exit(1)

    print(f"=== 1. Chunk count: {len(texts)} ===")
    for i, text in enumerate(texts):
        print(f"  [{i}] {len(text)} chars, ~{_approx_tokens(text)} tokens")

    print(f"\n=== 2. Chunk previews (first {_PREVIEW_CHARS} chars) ===")
    for i, text in enumerate(texts):
        preview = text[:_PREVIEW_CHARS].replace("\n", " ")
        print(f"  [{i}] {preview}...")

    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]

    archetype = ARCHETYPE_CONFIG[_ACTIVE_ARCHETYPE]
    query = archetype["archetype_focus"]
    print(f"\n=== 3. Retrieval query (archetype_focus: {_ACTIVE_ARCHETYPE}) ===")
    print(f"  {query}")

    query_vec = np.array(embedding_model.encode([query])[0])
    chunk_matrix = np.array([c.embedding for c in chunks])
    scores = chunk_matrix @ query_vec  # normalized embeddings -> dot product == cosine similarity
    ranked = np.argsort(scores)[::-1]

    top_n = min(5, len(ranked))
    print(f"\n=== 4. Top {top_n} cosine similarity scores ===")
    for rank, idx in enumerate(ranked[:top_n], start=1):
        print(f"  #{rank}: chunk [{idx}] — score {scores[idx]:.4f}")

    winner_idx = ranked[0]
    winner = chunks[winner_idx]
    print(f"\n=== 5. Winning chunk (index {winner_idx}, score {scores[winner_idx]:.4f}) ===")
    print(winner.text)

    prompt = PANELIST_SYSTEM_PROMPT.format(
        panelist_name=_PANELIST_NAME,
        archetype_title=archetype["archetype_title"],
        archetype_focus=archetype["archetype_focus"],
        archetype_lane=archetype["archetype_lane"],
        defense_type="thesis",
        other_subtype_line="",
        domain="<domain placeholder — not collected by this inspector>",
        topic="<topic placeholder — not collected by this inspector>",
        difficulty_level=_DIFFICULTY,
        retrieved_chunk=winner.text,
    )
    print("\n=== 6. Fully rendered PANELIST_SYSTEM_PROMPT ===")
    print(prompt)


if __name__ == "__main__":
    main()
