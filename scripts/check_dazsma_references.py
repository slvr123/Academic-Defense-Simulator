"""v2.0 M7 J6: does retrieval ever land on DAZSMA's "Appendix H – References" page?

Report only. M6's reference filter doesn't apply to DAZSMA (its references sit under
an appendix heading), so this checks whether that matters: every retrieving
archetype's top-1, the plain session of each recommended panel, and M6's 20 seeded
sessions per panel (the session ids check E's seeded mode uses), all on the
production ingest and retrievers. Page numbers only, never text.

Usage (repo root, backend/.venv): python scripts/check_dazsma_references.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from academic_defense_simulator.panel import PANEL_COMPOSITION  # noqa: E402
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG  # noqa: E402
from academic_defense_simulator.rag.retrieval import retrieve  # noqa: E402
from backend import embedder as backend_embedder  # noqa: E402
from backend.engine_adapter import RecordingEncoder, engine_chunks  # noqa: E402
from run_retrieval_eval import (  # noqa: E402
    LIVE_DOCUMENT_SHA,
    MODEL_DIR,
    PARSERS,
    RETRIEVING_ARCHETYPES,
    SEEDED_SESSIONS,
    load_document,
    production_session,
    recommended_panel,
)

DAZSMA = ROOT / "Group2_Library Management System for DAZSMA Documentation (1).pdf"
HEADING = "Appendix H"


def reference_pages(path: Path) -> list[int]:
    """1-based pages from the last line starting "Appendix H" (with "References" after
    it) to the end of the document. The last, because the contents page lists it too."""
    with fitz.open(str(path)) as pdf:
        texts = [page.get_text("text") for page in pdf]
    for index in reversed(range(len(texts))):
        if any(line.strip().startswith(HEADING) and "References" in line for line in texts[index].splitlines()):
            return list(range(index + 1, len(texts) + 1))
    raise SystemExit("STOP: no Appendix H references heading found")


def main() -> None:
    from tokenizers import Tokenizer

    embedder = backend_embedder.OnnxEmbedder.load(MODEL_DIR)
    counter = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    doc = load_document(DAZSMA, True, embedder, counter, PARSERS["production"])
    if doc.sha256 != LIVE_DOCUMENT_SHA:
        raise SystemExit("STOP: this isn't the DAZSMA file the evals used")
    pages = set(reference_pages(DAZSMA))

    def lands(chunk: int) -> bool:
        first, last = doc.pages_1based(chunk)
        return any(p in pages for p in range(first, last + 1))

    reference_chunks = [i for i in range(len(doc.chunks)) if lands(i)]
    chunks = engine_chunks(doc.matrix, doc.chunks)
    encoder = RecordingEncoder(embedder)
    top1 = {}
    for archetype in RETRIEVING_ARCHETYPES:
        [(index, _)] = retrieve(ARCHETYPE_CONFIG[archetype]["archetype_focus"], chunks, top_k=1, embedding_model=encoder)
        top1[archetype] = index
    plain, seeded = [], []
    for key in PANEL_COMPOSITION:
        panel = recommended_panel(key)
        plain += [index for index, _ in production_session(doc, panel, embedder)]
        for n in range(SEEDED_SESSIONS):
            seeded += [index for index, _ in production_session(doc, panel, embedder, f"m6-eval-{n}")]

    print(f"DAZSMA references: pages {min(pages)}-{max(pages)}; chunks touching them: "
          f"{[(i, doc.pages_1based(i)) for i in reference_chunks]}")
    print(f"top-1 picks on them: {sum(lands(i) for i in top1.values())} of {len(top1)} archetypes "
          f"(top-1 pages: {sorted({tuple(doc.pages_1based(i)) for i in top1.values()})})")
    print(f"plain session turns on them: {sum(lands(i) for i in plain)} of {len(plain)}")
    print(f"seeded session turns on them: {sum(lands(i) for i in seeded)} of {len(seeded)}")


if __name__ == "__main__":
    main()
