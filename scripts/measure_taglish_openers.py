"""M16 amendment 3: does the turn-1 opening question come out in Taglish?

Eval-only:

    backend/.venv/Scripts/python -u scripts/measure_taglish_openers.py [--n 10]
        [--out evals/v2.0-m16-taglish-openers.json]

Sets up N fresh AniCheck defenses in-process, exactly as scripts/measure_taglish_lines.py
does (thesis preset, panel_language "taglish", the backend's own setup_defense and ask, the
configured defense model), and asks each one its turn-1 opening question, nothing more. No
daily slot is used (no quota store); the Gemini calls are printed.

The check was fixed before the run: at least 8 of the 10 openers contain Filipino, counted
by measure_taglish_lines.py's FILIPINO_WORDS (at least one listed word).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from academic_defense_simulator.llm.provider import CallCounter  # noqa: E402
from backend.defense import setup_defense  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.embedder import OnnxEmbedder  # noqa: E402
from backend.schemas import PanelConfig  # noqa: E402
from backend.sessions import Session, SessionStatus  # noqa: E402
from backend.settings import Settings  # noqa: E402
from measure_closing_lines import ANICHECK  # noqa: E402
from measure_opening_order import THESIS_PANEL, ask_with_retries, ingest  # noqa: E402
from measure_taglish_lines import filipino_words  # noqa: E402

PASS_AT = 8


def one_opener(matrix, chunks, starts, settings: Settings, embedder) -> dict:
    session = Session(
        id=str(uuid.uuid4()),
        panel=PanelConfig(
            defense_type="thesis", domain=ANICHECK.domain, topic=ANICHECK.topic, archetype_keys=THESIS_PANEL,
            panel_language="taglish",
        ),
        filename=ANICHECK.path.name, last_access=time.monotonic(), status=SessionStatus.READY,
        matrix=matrix, chunks=chunks, chapter_starts=starts,
    )
    calls = CallCounter()
    defense = setup_defense(
        session, gemini_providers_factory(settings)(calls), calls, embedder, settings.gemini_model,
        attribution_notes=settings.attribution_notes, lit_review_access=settings.lit_review_access,
        opening_chapter_filter=settings.opening_chapter_filter, closing_lines="llm",
    )
    assert defense.language == "taglish"
    question = ask_with_retries(defense)
    assert question.turn == 1
    return {
        "turn": question.turn, "archetype_key": question.panelist.archetype_key, "panelist": question.panelist.name,
        "title": question.panelist.title, "pages": [question.sources[0].page_start, question.sources[0].page_end],
        "question": question.question, "filipino_words": filipino_words(question.question),
        "gemini_calls": calls.total,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "v2.0-m16-taglish-openers.json")
    args = parser.parse_args()
    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    matrix, chunks, starts = ingest(ANICHECK.path, embedder)
    print(f"model={settings.gemini_model} panel_language=taglish openers={args.n}", flush=True)
    openers = []
    for i in range(args.n):
        opener = one_opener(matrix, chunks, starts, settings, embedder)
        openers.append(opener)
        print(f"  {i + 1:2} {opener['archetype_key']} pp.{opener['pages']} calls={opener['gemini_calls']} "
              f"filipino={opener['filipino_words']}", flush=True)
    with_filipino = sum(1 for o in openers if o["filipino_words"])
    check = {"rule": f"at least {PASS_AT} of {args.n} turn-1 openers contain Filipino",
             "with_filipino": with_filipino, "passed": with_filipino >= PASS_AT}
    total_calls = sum(o["gemini_calls"] for o in openers)
    args.out.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.gemini_model, "document": ANICHECK.path.name, "panel": THESIS_PANEL,
        "panel_language": "taglish", "openers": openers, "check": check, "gemini_calls": total_calls,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"check: {check}", flush=True)
    print(f"{total_calls} Gemini calls -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
