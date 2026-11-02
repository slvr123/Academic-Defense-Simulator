"""v2.0 M12 Decision 1: does the opening round walk forward through the paper?

Eval-only. For each document, runs N defenses in-process through the backend's own
setup_defense and ask() (so the seated order is the product's, opening_order included)
and generates only the opening round: one new-topic question per domain panelist.
No answers are written or scored. Between questions the open turn is recorded with a
synthetic strong score (no LLM call), so v1's engine opens the next panelist's topic
instead of a follow-up, exactly as it would after a strong answer.

A round passes if the chapters its questions cite never go backwards. Chapters come
from the PDF's own "CHAPTER n" headings: a page carrying exactly one such heading
(table-of-contents pages carry several, or dotted leaders) starts that chapter; pages
before Chapter 1 are chapter 0. The cited page is the question's source page_start.

    backend/.venv/Scripts/python scripts/measure_opening_order.py
        [--sessions 5] [--out evals/v2.0-m12-opening-order.json]

Spends Gemini calls on the backend's configured defense model (persona generation and
the opening-round questions). On a 503 it waits and retries the same question; it never
changes the model. The output records keys, pages and chapters only, no passage text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import fitz
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from academic_defense_simulator.engine import record_scored_turn  # noqa: E402
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError  # noqa: E402
from academic_defense_simulator.models.answer_score import AnswerScore  # noqa: E402
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY  # noqa: E402
from backend.defense import ask, setup_defense  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.embedder import EMBEDDING_DIM, OnnxEmbedder  # noqa: E402
from backend.llm_errors import is_panel_busy  # noqa: E402
from backend.pdf_ingest import parse_pdf  # noqa: E402
from backend.schemas import PanelConfig  # noqa: E402
from backend.sessions import ChunkMeta, Session, SessionStatus  # noqa: E402
from backend.settings import Settings  # noqa: E402

# The Recommended thesis preset (frontend/src/lib/presets.ts), which seats the
# Research Problem & Objectives Reviewer.
THESIS_PANEL = ["methodology_expert", "problem_objectives_reviewer", "results_conclusions_reviewer"]


@dataclass(frozen=True)
class Document:
    label: str
    path: Path
    domain: str | None  # None: the backend extracts it, as for an upload without details
    topic: str | None


DOCUMENTS = [
    Document(
        "AniCheck", REPO_ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf",
        "Information Technology", "AniCheck: a mobile app for rice leaf disease identification",
    ),
    # Extracted at setup (one call per session), so no document text is copied here.
    Document("sample3", REPO_ROOT / "sample3.pdf", None, None),
]

HEADING = re.compile(r"^\s*CHAPTER\s+(\d+)\b(.*)$", re.IGNORECASE | re.MULTILINE)
BUSY_WAIT_S = 60
BUSY_RETRIES = 10


def chapter_starts(path: Path) -> dict[int, int]:
    """Chapter number -> its first 1-based page, from pages with exactly one heading."""
    starts: dict[int, int] = {}
    with fitz.open(path) as pdf:
        for index, page in enumerate(pdf):
            found = {int(m.group(1)) for m in HEADING.finditer(page.get_text()) if "..." not in m.group(2)}
            if len(found) == 1:
                starts.setdefault(found.pop(), index + 1)
    return starts


def chapter_of(page: int, starts: dict[int, int]) -> int:
    return max((n for n, first in starts.items() if first <= page), default=0)


def passes(chapters: list[int]) -> bool:
    return all(a <= b for a, b in zip(chapters, chapters[1:]))


def ingest(path: Path, embedder: OnnxEmbedder) -> tuple[np.ndarray, list[ChunkMeta]]:
    filtered = parse_pdf(path)
    matrix = np.empty((len(filtered.chunks), EMBEDDING_DIM), dtype=np.float32)
    for index, chunk in enumerate(filtered.chunks):
        matrix[index] = embedder.encode_one(chunk.text)
    chunks = [
        ChunkMeta(chunk_id=i, text=c.text, page_start=c.page_start, page_end=c.page_end, attribution=a)
        for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))
    ]
    return matrix, chunks


def ask_with_retries(defense):
    for attempt in range(1, BUSY_RETRIES + 1):
        try:
            return ask(defense)
        except LLMProviderError as exc:
            if attempt == BUSY_RETRIES:
                raise
            wait = BUSY_WAIT_S if is_panel_busy(exc) else 10
            print(f"    ask failed ({type(exc).__name__}); waiting {wait}s, attempt {attempt}", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


# Recorded in place of an answer: strong, so v1 opens the next panelist's topic.
SKIPPED = AnswerScore(
    clarity=5, depth=5, grounding=5, difficulty_delta=0, primary_gap=None,
    answer_summary="(not answered: opening-round measurement)",
)


def one_round(doc: Document, matrix, chunks, starts, settings, embedder) -> dict:
    session = Session(
        id=str(uuid.uuid4()),
        panel=PanelConfig(defense_type="thesis", domain=doc.domain, topic=doc.topic, archetype_keys=THESIS_PANEL),
        filename=doc.path.name, last_access=time.monotonic(), status=SessionStatus.READY,
        matrix=matrix, chunks=chunks,
    )
    calls = CallCounter()
    providers = gemini_providers_factory(settings)(calls)
    defense = setup_defense(
        session, providers, calls, embedder, settings.gemini_model,
        attribution_notes=settings.attribution_notes, lit_review_access=settings.lit_review_access,
    )
    domain_count = sum(p.archetype_key != DEVILS_ADVOCATE_KEY for p in defense.session.panel)
    questions = []
    for _ in range(domain_count):
        question = ask_with_retries(defense)
        source = question.sources[0]
        questions.append({
            "turn": question.turn,
            "archetype_key": question.panelist.archetype_key,
            "origin": source.origin,
            "page": source.page_start,
            "chapter": chapter_of(source.page_start, starts),
        })
        turn = defense.pending.model_copy(update={"answer": "", "score": SKIPPED})
        record_scored_turn(defense.session, turn)
        defense.pending = defense.pending_source = None
    chapters = [q["chapter"] for q in questions]
    return {
        "document": doc.label,
        "session_id": session.id,
        "seated": [p.archetype_key for p in defense.session.panel],
        "questions": questions,
        "passes": passes(chapters),
        "gemini_calls": calls.total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions", type=int, default=5, help="per document")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "v2.0-m12-opening-order.json")
    args = parser.parse_args()

    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    rows = []
    for doc in DOCUMENTS:
        starts = chapter_starts(doc.path)
        if 1 not in starts:
            sys.exit(f"HALT: no detectable CHAPTER headings in {doc.label}; pick another eval document")
        print(f"{doc.label}: chapter starts {starts}", flush=True)
        matrix, chunks = ingest(doc.path, embedder)
        for n in range(1, args.sessions + 1):
            row = one_round(doc, matrix, chunks, starts, settings, embedder)
            row["chapter_starts"] = starts
            rows.append(row)
            order = " -> ".join(f"{q['archetype_key']} p{q['page']} ch{q['chapter']}" for q in row["questions"])
            print(f"  {doc.label} #{n} {'PASS' if row['passes'] else 'FAIL'}: {order} ({row['gemini_calls']} calls)",
                  flush=True)
            args.out.write_text(json.dumps({"model": settings.gemini_model, "rows": rows}, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
        del matrix, chunks
    passed = sum(r["passes"] for r in rows)
    result = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.gemini_model,
        "panel": THESIS_PANEL,
        "passed": passed,
        "total": len(rows),
        "rows": rows,
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\n{passed} of {len(rows)} opening rounds pass")


if __name__ == "__main__":
    main()
