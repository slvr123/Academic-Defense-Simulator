"""M16 Decision 5: what a Taglish panel sounds like, and the Taglish questions for
Decision 4's scoring-parity pairs.

Eval-only:

    backend/.venv/Scripts/python -u scripts/measure_taglish_lines.py [--sessions 2]
        [--out evals/v2.0-m16-taglish-lines.json]

Runs AniCheck defenses in-process through the backend's own setup_defense, ask and
answer, on the thesis preset with panel_language "taglish", the backend's configured
defense model and ADS_CLOSING_LINES=llm, so every question path and closing line is the
product's. The answers are M15 E1's pre-written English ones (a candidate may answer in
any language, Decision 1), on a planned band per panelist so follow-ups happen. A
session stops once the Terror Panelist has asked: its answer and the report are English
anyway. No daily slot is used (no quota store); the Gemini calls are printed.

The 15 lines Decision 5 lists are chosen by a rule fixed before the run: each session's
three opening questions (6), its first two follow-ups (4), its Terror Panelist challenge
(2), and session 1's three closing lines (3). Everything else generated is kept here too.

Every question is saved with the exact chunk text the scorer would see for it, so
Decision 4 can score answers to it as the product does.

After the Decision 2 amendment (the opener prompt's language line moved), the run was
repeated with a check fixed before it ran: at least 5 of the 6 openers contain Filipino.
"Contains Filipino" is mechanical, not a judgement: at least one word from FILIPINO_WORDS
below. `--recount FILE` applies the same count to an earlier run without any calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from academic_defense_simulator.llm.provider import CallCounter  # noqa: E402
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY  # noqa: E402
from backend.defense import setup_defense  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.embedder import OnnxEmbedder  # noqa: E402
from backend.schemas import PanelConfig  # noqa: E402
from backend.sessions import Session, SessionStatus  # noqa: E402
from backend.settings import Settings  # noqa: E402
from measure_closing_lines import ANICHECK, ANSWERS, ClosingLog, answer_with_retries  # noqa: E402
from measure_opening_order import THESIS_PANEL, ask_with_retries, ingest  # noqa: E402

# Per session, the planned band of each seated domain panelist, in seating order. Mid and
# weak blocks draw follow-ups; strong ones usually hand off at once.
PLANS = [["weak", "mid", "strong"], ["mid", "strong", "weak"]]

# Common Filipino function words and discourse words, fixed before the amended run. Words
# that are also ordinary English ("at", "may", "din", "okay") are left out so English can't match.
FILIPINO_WORDS = frozenset("""
    ang ng mga sa si ni kay na ay ba po opo pa nga lang muna rin naman talaga kasi
    pero sige kaya kung para dahil kapag tapos hindi wala meron mayroon dapat lalo
    ito iyan yan iyon yon yung dito diyan rito riyan doon ganito ganyan ganoon ganun
    ako ko ka mo ikaw kayo niyo ninyo inyo inyong tayo natin namin kami sila nila siya niya
    ano paano bakit saan kailan sino alin ilan
    tungkol hanggang sabi sinabi kanina balikan medyo paulit ulit bahagi bahaging tanong
    sagot salamat oo ngayon muli tingnan pag usapan
""".split())


def filipino_words(text: str) -> list[str]:
    """The FILIPINO_WORDS in `text`, in order. Apostrophe forms split: 'yan -> yan, na't -> na."""
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w in FILIPINO_WORDS]


def count_by_kind(fifteen: list[dict]) -> dict:
    by_kind: dict[str, dict] = {}
    for n, item in enumerate(fifteen, 1):
        words = filipino_words(item.get("question") or item.get("line") or "")
        entry = by_kind.setdefault(item["kind"], {"lines": 0, "with_filipino": 0, "all_english": []})
        entry["lines"] += 1
        if words:
            entry["with_filipino"] += 1
        else:
            entry["all_english"].append(n)
        item["filipino_words"] = words
    return by_kind


def report_count(fifteen: list[dict]) -> dict:
    by_kind = count_by_kind(fifteen)
    total = sum(k["with_filipino"] for k in by_kind.values())
    print(f"lines with Filipino: {total} of {len(fifteen)}", flush=True)
    for kind, entry in by_kind.items():
        print(f"  {kind}: {entry['with_filipino']} of {entry['lines']} (all English: {entry['all_english']})", flush=True)
    openers = by_kind.get("opening", {"with_filipino": 0})
    check = {"rule": "at least 5 of the 6 openers contain Filipino", "openers_with_filipino": openers["with_filipino"],
             "passed": openers["with_filipino"] >= 5}
    print(f"check: {check}", flush=True)
    for n, item in enumerate(fifteen, 1):
        print(f"  {n:2} {item['kind']:<16} {item['filipino_words']}", flush=True)
    return {"by_kind": by_kind, "lines_with_filipino": total, "check": check}


def asked(defense, question, session: int) -> dict:
    """One question as the candidate saw it, with the chunk the scorer reads for it."""
    turn = defense.pending
    return {
        "session": session,
        "turn": question.turn,
        "archetype_key": question.panelist.archetype_key,
        "panelist": question.panelist.name,
        "title": question.panelist.title,
        "path": question.sources[0].origin,
        "difficulty": question.difficulty,
        "pages": [question.sources[0].page_start, question.sources[0].page_end],
        "question": question.question,
        "grounding_reference": question.grounding_reference,
        "chunk_text": turn.chunk_text,
    }


def one_session(index: int, matrix, chunks, starts, settings: Settings, embedder, log: ClosingLog) -> dict:
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
    seated = [p.archetype_key for p in defense.session.panel if p.archetype_key != DEVILS_ADVOCATE_KEY]
    plan = dict(zip(seated, PLANS[index % len(PLANS)], strict=True))
    log.lines.clear()
    question = ask_with_retries(defense)
    questions = [asked(defense, question, index + 1)]
    closings: list[dict] = []
    used: dict[str, int] = {}
    while question.panelist.archetype_key != DEVILS_ADVOCATE_KEY:
        key = question.panelist.archetype_key
        texts = ANSWERS[key][plan[key]]
        text = texts[min(used.get(key, 0), len(texts) - 1)]
        used[key] = used.get(key, 0) + 1
        result, _ms = answer_with_retries(defense, text)
        questions[-1].update(answer=text, planned=plan[key], total=result.evaluation.total)
        print(f"    turn {question.turn} {key} [{questions[-1]['path']}] total={result.evaluation.total}: {question.question}", flush=True)
        if result.closing is not None:
            logged = log.lines.get(question.turn, {})
            closings.append({
                "session": index + 1, "turn": question.turn, "archetype_key": key,
                "panelist": result.closing.panelist.name, "title": result.closing.panelist.title,
                "line": result.closing.line, "source": logged.get("source"), "reason": logged.get("reason"),
            })
            print(f"      closing [{logged.get('source')}/{logged.get('reason')}]: {result.closing.line}", flush=True)
        question = result.next_question
        assert question is not None, "a defense ended before the Terror Panelist spoke"
        questions.append(asked(defense, question, index + 1))
    print(f"    turn {question.turn} {question.panelist.archetype_key} [{questions[-1]['path']}]: {question.question}", flush=True)
    return {"session": index + 1, "session_id": session.id, "seated": seated, "plan": plan,
            "questions": questions, "closings": closings, "gemini_calls": calls.total, "by_role": dict(calls.by_stage)}


def the_fifteen(sessions: list[dict]) -> list[dict]:
    """Decision 5's lines, by the rule in the module docstring."""
    picked = []
    for s in sessions:
        qs = s["questions"]
        picked += [dict(q, kind="opening") for q in qs if q["path"] == "new_topic"][:3]
        picked += [dict(q, kind="follow_up") for q in qs if q["path"] == "follow_up"][:2]
        picked += [dict(q, kind="terror_panelist") for q in qs if q["path"] == "devils_advocate"][:1]
    picked += [dict(c, kind="closing") for c in sessions[0]["closings"][:3]]
    return picked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "v2.0-m16-taglish-lines-2.json")
    parser.add_argument("--recount", type=Path, help="count Filipino in an earlier run's 15 lines; no calls")
    args = parser.parse_args()
    if args.recount is not None:
        report_count(json.loads(args.recount.read_text(encoding="utf-8"))["decision_5_lines"])
        return 0
    if args.sessions > 3:
        raise SystemExit("M16 Decision 5 needs two sessions; three at most, for a retry")
    logging.basicConfig(level=logging.WARNING)
    log = ClosingLog()
    logging.getLogger("backend.closing").addHandler(log)
    logging.getLogger("backend.closing").setLevel(logging.INFO)
    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    matrix, chunks, starts = ingest(ANICHECK.path, embedder)
    print(f"model={settings.gemini_model} panel_language=taglish closing_lines=llm sessions={args.sessions}", flush=True)
    sessions = []
    for i in range(args.sessions):
        print(f"  session {i + 1}", flush=True)
        sessions.append(one_session(i, matrix, chunks, starts, settings, embedder, log))
        print(f"  session {i + 1}: {len(sessions[-1]['questions'])} questions, {len(sessions[-1]['closings'])} closings, "
              f"{sessions[-1]['gemini_calls']} Gemini calls {sessions[-1]['by_role']}", flush=True)
    fifteen = the_fifteen(sessions)
    count = report_count(fifteen)
    args.out.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.gemini_model,
        "document": ANICHECK.path.name,
        "panel": THESIS_PANEL,
        "panel_language": "taglish",
        "sessions": sessions,
        "decision_5_lines": fifteen,
        "filipino_count": count,
        "gemini_calls": sum(s["gemini_calls"] for s in sessions),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    kinds = [line["kind"] for line in fifteen]
    print(f"Decision 5 lines: {len(fifteen)} {dict((k, kinds.count(k)) for k in dict.fromkeys(kinds))} -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
