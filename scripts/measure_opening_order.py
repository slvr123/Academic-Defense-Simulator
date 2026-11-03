"""Does the opening round walk forward through the paper? (v2.0 M12 D1, M14 D3)

Eval-only. Two commands:

    backend/.venv/Scripts/python scripts/measure_opening_order.py rounds
        [--sessions 5] [--chapter-filter] [--out evals/v2.0-m14-opening-order.json]
    backend/.venv/Scripts/python scripts/measure_opening_order.py hypothesis

`rounds` runs N defenses per document in-process through the backend's own
setup_defense and ask() (so the seated order and, with --chapter-filter, M14's
opening-question filter are the product's) and generates only the opening round: one
new-topic question per domain panelist. No answers are written or scored. Between
questions the open turn is recorded with a synthetic strong score (no LLM call), so v1's
engine opens the next panelist's topic instead of a follow-up, as after a strong answer.

A round passes if the chapters its questions cite never go backwards. Chapters come
from the product's own detector (backend/page_filter.chapter_starts, M14 D1); pages
before Chapter 1 are chapter 0. The cited page is the question's source page_start.
Each question also records the filter's outcome from the backend's "opening question"
log line (applied, fallback_no_candidates, flag_off, no_chapter_map).

`hypothesis` (M14 E1) is embeddings only, zero Gemini calls: for the Research Problem &
Objectives Reviewer's focus query on AniCheck, the top 10 chunks by cosine score with
their pages and chapters, and the best Chapter 1 chunk's score and rank.

Spends Gemini calls (`rounds` only) on the backend's configured defense model: persona
generation and the opening-round questions. On a 503 it waits and retries the same
question; it never changes the model. The output records keys, pages and chapters, and
the question text for the public AniCheck sample only; never sample3's text.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from academic_defense_simulator.engine import record_scored_turn  # noqa: E402
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError  # noqa: E402
from academic_defense_simulator.models.answer_score import AnswerScore  # noqa: E402
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY  # noqa: E402
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG  # noqa: E402
from backend.chapter_filter import target_chapter  # noqa: E402
from backend.defense import ask, setup_defense  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.embedder import EMBEDDING_DIM, OnnxEmbedder  # noqa: E402
from backend.llm_errors import is_panel_busy  # noqa: E402
from backend.page_filter import chapter_of  # noqa: E402
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
    public: bool  # its text may be committed (the bundled AniCheck sample only)


DOCUMENTS = [
    Document(
        "AniCheck", REPO_ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf",
        "Information Technology", "AniCheck: a mobile app for rice leaf disease identification", True,
    ),
    # Extracted at setup (one call per session), so no document text is copied here.
    Document("sample3", REPO_ROOT / "sample3.pdf", None, None, False),
]

BUSY_WAIT_S = 60
BUSY_RETRIES = 10
OPENING_LINE = re.compile(r"opening question .* archetype=(?P<key>\S+) .*chapter_filter=(?P<outcome>\S+) page=(?P<page>\d+)")


def passes(chapters: list[int]) -> bool:
    return all(a <= b for a, b in zip(chapters, chapters[1:]))


def one_based(starts: dict[int, int]) -> dict[int, int]:
    return {n: page + 1 for n, page in sorted(starts.items())}


def ingest(path: Path, embedder: OnnxEmbedder) -> tuple[np.ndarray, list[ChunkMeta], dict[int, int] | None]:
    filtered = parse_pdf(path)
    matrix = np.empty((len(filtered.chunks), EMBEDDING_DIM), dtype=np.float32)
    for index, chunk in enumerate(filtered.chunks):
        matrix[index] = embedder.encode_one(chunk.text)
    chunks = [
        ChunkMeta(chunk_id=i, text=c.text, page_start=c.page_start, page_end=c.page_end, attribution=a)
        for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))
    ]
    return matrix, chunks, filtered.chapter_starts


class OpeningLog(logging.Handler):
    """Collects backend.defense's "opening question" lines: archetype -> outcome."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.outcomes: dict[str, str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        match = OPENING_LINE.search(record.getMessage())
        if match:
            self.outcomes[match.group("key")] = match.group("outcome")


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


def one_round(doc: Document, matrix, chunks, starts, settings, embedder, chapter_filter: bool, log: OpeningLog) -> dict:
    session = Session(
        id=str(uuid.uuid4()),
        panel=PanelConfig(defense_type="thesis", domain=doc.domain, topic=doc.topic, archetype_keys=THESIS_PANEL),
        filename=doc.path.name, last_access=time.monotonic(), status=SessionStatus.READY,
        matrix=matrix, chunks=chunks, chapter_starts=starts,
    )
    calls = CallCounter()
    providers = gemini_providers_factory(settings)(calls)
    defense = setup_defense(
        session, providers, calls, embedder, settings.gemini_model,
        attribution_notes=settings.attribution_notes, lit_review_access=settings.lit_review_access,
        opening_chapter_filter=chapter_filter,
    )
    log.outcomes.clear()
    domain_count = sum(p.archetype_key != DEVILS_ADVOCATE_KEY for p in defense.session.panel)
    questions = []
    for _ in range(domain_count):
        question = ask_with_retries(defense)
        source = question.sources[0]
        key = question.panelist.archetype_key
        row = {
            "turn": question.turn,
            "archetype_key": key,
            "origin": source.origin,
            "page": source.page_start,
            "chapter": chapter_of(source.page_start - 1, starts) if starts else None,
            "target_chapter": target_chapter(key, starts) if starts else None,
            "chapter_filter": log.outcomes.get(key),
        }
        if doc.public:
            row["question"] = question.question
        questions.append(row)
        turn = defense.pending.model_copy(update={"answer": "", "score": SKIPPED})
        record_scored_turn(defense.session, turn)
        defense.pending = defense.pending_source = None
    chapters = [q["chapter"] for q in questions]
    return {
        "document": doc.label,
        "session_id": session.id,
        "seated": [p.archetype_key for p in defense.session.panel],
        "questions": questions,
        "passes": None not in chapters and passes(chapters),
        "gemini_calls": calls.total,
    }


def rounds(args: argparse.Namespace) -> None:
    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    log = OpeningLog()
    logging.getLogger("backend.defense").addHandler(log)
    logging.getLogger("backend.defense").setLevel(logging.INFO)
    rows = []

    def write(extra: dict) -> None:
        body = {"model": settings.gemini_model, "chapter_filter": args.chapter_filter, "panel": THESIS_PANEL,
                "lit_review_access": settings.lit_review_access, **extra, "rows": rows}
        args.out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    for doc in DOCUMENTS:
        matrix, chunks, starts = ingest(doc.path, embedder)
        if starts is None:
            sys.exit(f"HALT: no chapter map for {doc.label}; pick another eval document")
        print(f"{doc.label}: chapter starts (1-based) {one_based(starts)}", flush=True)
        for n in range(1, args.sessions + 1):
            row = one_round(doc, matrix, chunks, starts, settings, embedder, args.chapter_filter, log)
            row["chapter_starts_1_based"] = one_based(starts)
            rows.append(row)
            order = " -> ".join(
                f"{q['archetype_key']} p{q['page']} ch{q['chapter']} [{q['chapter_filter']}]" for q in row["questions"]
            )
            print(f"  {doc.label} #{n} {'PASS' if row['passes'] else 'FAIL'}: {order} ({row['gemini_calls']} calls)",
                  flush=True)
            write({})
        del matrix, chunks
    passed = sum(r["passes"] for r in rows)
    write({"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "passed": passed, "total": len(rows)})
    print(f"\n{passed} of {len(rows)} opening rounds pass")


def hypothesis(_args: argparse.Namespace) -> None:
    """M14 E1: where the Problem & Objectives Reviewer's focus query lands on AniCheck."""
    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    doc = DOCUMENTS[0]
    matrix, chunks, starts = ingest(doc.path, embedder)
    assert starts is not None
    key = "problem_objectives_reviewer"
    scores = matrix @ embedder.encode_one(ARCHETYPE_CONFIG[key]["archetype_focus"])
    ranked = sorted(range(len(chunks)), key=lambda i: -float(scores[i]))
    print(f"{doc.label}: {len(chunks)} chunks, chapter starts (1-based) {one_based(starts)}")
    print(f"focus query of {key} (unchanged, ARCHETYPE_CONFIG):")
    print(f"  {ARCHETYPE_CONFIG[key]['archetype_focus']}")
    print(f"{'rank':>4} {'score':>7} {'page_start':>10} {'chapter':>7}  lit-review tag")
    for rank, i in enumerate(ranked[:10], start=1):
        meta = chunks[i]
        print(f"{rank:>4} {scores[i]:7.4f} {meta.page_start + 1:>10} {chapter_of(meta.page_start, starts):>7}  {meta.attribution or '-'}")
    chapter_one = [i for i in ranked if chapter_of(chunks[i].page_start, starts) == 1]
    if chapter_one:
        best = chapter_one[0]
        print(f"best Chapter 1 chunk: rank {ranked.index(best) + 1} of {len(chunks)}, score {scores[best]:.4f}, "
              f"page_start {chunks[best].page_start + 1}; top score {scores[ranked[0]]:.4f}, "
              f"gap {scores[ranked[0]] - scores[best]:.4f}")
    else:
        print("no chunk starts in Chapter 1")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("rounds")
    run.add_argument("--sessions", type=int, default=5, help="per document")
    run.add_argument("--chapter-filter", action="store_true", help="M14's opening-question filter on")
    run.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "v2.0-m14-opening-order.json")
    commands.add_parser("hypothesis")
    args = parser.parse_args()
    (rounds if args.command == "rounds" else hypothesis)(args)


if __name__ == "__main__":
    main()
