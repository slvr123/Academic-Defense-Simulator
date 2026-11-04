"""M15 E1: are the outgoing panelist's closing lines neutral? And E2's hand-offs, and
the hand-off latency, from the same runs.

Eval-only:

    backend/.venv/Scripts/python -u scripts/measure_closing_lines.py [--sessions 10]
        [--out evals/v2.0-m15-closing-lines.json]

Runs AniCheck defenses in-process through the backend's own setup_defense, ask and
answer (so the hand-off decision, the closing call, its fallback and its timing are the
product's), on the thesis preset and the backend's configured defense model, with
ADS_CLOSING_LINES=llm. Answers come from ANSWERS below, written by Claude from the paper,
each panelist's block on a planned band: strong (one answer, so the hand-off follows
it), or mid or weak (three answers, so the hand-off follows the second follow-up). The
plan rotates so the hand-offs split roughly into thirds; the band recorded is the one
the real score gives (strong 12+, weak 6 or less, of 15), not the plan.

A session stops once the Terror Panelist's question exists, after its third hand-off:
its answer and the report add nothing to E1. No daily slot is used (no quota store);
the Gemini calls are spent on the shared key, and printed. On a failed turn it waits
and sends the same answer again; it never changes the model.

With --live DIR, the answer a hand-off follows is written live, to the question actually
asked: every answer in a strong block, and the third answer in a mid block. The script
writes DIR/question.json and waits for DIR/answer-<session>-<turn>.txt. Pre-written
answers didn't fit the live questions closely enough to score strong or mid (the first
three sessions, all pre-written, gave 8 weak hand-offs of 9). --plans sets each
session's planned bands, e.g. "strong,mid,strong;mid,strong,mid".

The output is AniCheck only (the public sample): questions, answers, closing lines and
the incoming openers, with the closing call's own log fields (source, reason, call_ms,
waited_ms). Classification is done by hand afterwards, in the results doc.
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

from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError  # noqa: E402
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY  # noqa: E402
from backend.defense import answer, setup_defense  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.embedder import OnnxEmbedder  # noqa: E402
from backend.llm_errors import is_panel_busy  # noqa: E402
from backend.schemas import PanelConfig  # noqa: E402
from backend.sessions import Session, SessionStatus  # noqa: E402
from backend.settings import Settings  # noqa: E402
from measure_opening_order import DOCUMENTS, THESIS_PANEL, ask_with_retries, ingest  # noqa: E402

ANICHECK = next(d for d in DOCUMENTS if d.label == "AniCheck")
BUSY_WAIT_S = 60
BUSY_RETRIES = 10
CLOSING_LINE = re.compile(
    r"closing session=(?P<session>\S+) turn=(?P<turn>\d+) archetype=(?P<key>\S+) source=(?P<source>\S+) "
    r"reason=(?P<reason>\S+) words=(?P<words>\d+) call_ms=(?P<call_ms>\S+) waited_ms=(?P<waited_ms>\d+)"
)

# Written from the AniCheck paper (frontend/public/sample/sample-capstone-anicheck.pdf):
# per domain panelist, per band, the answers for its opening question and two follow-ups.
ANSWERS: dict[str, dict[str, list[str]]] = {
    "problem_objectives_reviewer": {
        "strong": [
            "We limited AniCheck to four foliar diseases, bacterial leaf blight, brown spot, leaf smut and tungro, plus healthy leaves, because those were the diseases the farmers named most often in our initial interviews and because their symptoms show on the leaf, which a phone camera can capture. Sheath blight and false smut show mainly on the stem and panicle, so a leaf photo can't diagnose them, and we put them in the recommendations instead. We also chose not to recommend chemicals or doses: the objective is decision support, prescribing pesticides is outside our competence, so the app returns an identification and a confidence percentage and leaves the treatment to a technician.",
            "To be precise about what the scope excludes: no panicle, stem or root diseases, no iOS build, only Android 9.0 and above, and images from one barangay in one six-week window of the 2024 wet season. Each exclusion is stated in Scope and Limitations, and field images, wider disease coverage and an iOS version are our first, second and fifth recommendations, so the boundary is deliberate rather than accidental.",
            "The general objective was an offline app that identifies common rice leaf diseases, and each specific objective maps to a research question: the dataset and transfer learning answer question 3 on accuracy, and the ISO/IEC 25010 evaluation answers question 4 on acceptability. Question 1 on farmer practices is answered by the 24 interviews in Chapter 4, where 19 farmers relied on a neighbor and 8 had applied a wrong treatment.",
        ],
        "mid": [
            "We chose the four diseases because farmers mentioned them in the interviews and they appear on the leaves. Other diseases were left out because of time. The app is only meant to help farmers, not to replace the technician.",
            "As I said, the scope came from the interviews. We think the four diseases cover most of what the farmers see, and we can add more diseases in a later version.",
            "I agree it is a limitation. Our objective was mainly to show that the app works offline on the phone, so we kept the scope small for the capstone.",
        ],
        "weak": [
            "We just picked the diseases that are common in rice.",
            "I'm not sure, I would have to check with my groupmates.",
            "I think it's fine because the farmers liked the app.",
        ],
    },
    "methodology_expert": {
        "strong": [
            "The 3,240 images came from eleven fields in Barangay Masagana over six weeks of the 2024 wet season. Each leaf was detached and shot against white cardboard between 8 and 10 AM, so lighting and background were controlled, and every label was confirmed on site by the municipal agriculture technician, who discarded anything he couldn't classify confidently. We split 80:20 stratified, 2,592 training and 648 test images, and augmented only the training set, more heavily for tungro because it was 12.7% of the data. I accept the trade-off: the 94.7% measures detached leaves in morning light, not field photos, which is why our first recommendation is field images from more municipalities.",
            "On sampling: the thirty respondents were purposively chosen, active rice growers who own an Android phone, from the same barangay as the dataset, and each used the app once for about thirty minutes right after a demonstration. That design measures first impressions from people close to the data source, not independent long-term use, and we state it in the limitations; a longitudinal, unsupervised study across a full season is our fourth recommendation.",
            "The test set is a stratified 20% held out from the same collection, so it shares the capture conditions of the training images. That makes 94.7% an in-distribution figure. The validation study we recommend compares the app's identifications with a technician's judgment on the same leaves in the field, which is the standard the tool is meant to approximate.",
        ],
        "mid": [
            "We collected the images ourselves in the barangay and had a technician label them. We used an 80:20 split and augmentation to make the model better. The thirty respondents were chosen from farmers who have Android phones.",
            "The controlled background was to make the images consistent. We think it still works on real photos because the augmentation adds rotation and brightness changes.",
            "We used purposive sampling because we needed farmers who grow rice and have phones. A bigger sample would be better but we had limited time.",
        ],
        "weak": [
            "We took pictures of the leaves and trained the model.",
            "I don't remember the exact number of fields.",
            "We followed what our adviser told us to do.",
        ],
    },
    "results_conclusions_reviewer": {
        "strong": [
            "The weakest class is bacterial leaf blight, with recall 0.813: 102 of 125 test images were correct and 16 went to brown spot, which matters because the two need different treatments. That lines up with reliability being the only ISO/IEC 25010 characteristic below Very Acceptable, at 4.09 against 4.42 for usability; respondents trusted their own use of the app more than its answers, and several leaves carried more than one condition, which a single-class output can't express. So we concluded the app is a preliminary decision-support aid and shouldn't replace a technician where a costly treatment follows.",
            "Table 4 shows the errors are concentrated, not spread: of the 23 misclassified bacterial leaf blight images, 16 went to brown spot, and both start as yellowing along the leaf margin. Healthy leaves were almost never wrong, recall 0.994, so the practical risk is a farmer treating a bacterial infection as a fungal one. That is why the conclusion limits the app to preliminary support and recommends validating it against a technician on the same leaves.",
            "Our 94.7% is below the 99% reported on PlantVillage, and we attribute that mainly to scale: 3,240 images across five classes is an order of magnitude smaller. We don't claim the field accuracy is 94.7%; the limitations say the transfer to attached leaves, complex backgrounds and variable light was not established.",
        ],
        "mid": [
            "The model got 94.7% accuracy, which is high. Bacterial leaf blight was the hardest class. The respondents rated the app 4.31 overall, which means Very Acceptable.",
            "Reliability was a little lower at 4.09 because some farmers thought the result was wrong. We think more data will fix it.",
            "The accuracy is lower than the PlantVillage studies because our dataset is smaller.",
        ],
        "weak": [
            "The results were good and the farmers were satisfied.",
            "I think the accuracy is around ninety percent.",
            "We did not have time to analyze that part in detail.",
        ],
    },
}

# The planned band of each domain block, by seat, per session; rotates through thirds.
PLANS = [["strong", "mid", "weak"], ["mid", "weak", "strong"], ["weak", "strong", "mid"]]


def band(total: int) -> str:
    return "strong" if total >= 12 else "weak" if total <= 6 else "mid"


class ClosingLog(logging.Handler):
    """Collects backend.closing's lines: (turn) -> the logged fields."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: dict[int, dict] = {}

    def emit(self, record: logging.LogRecord) -> None:
        match = CLOSING_LINE.search(record.getMessage())
        if match:
            self.lines[int(match.group("turn"))] = match.groupdict()


def answer_with_retries(defense, text: str):
    for attempt in range(1, BUSY_RETRIES + 1):
        try:
            started = time.perf_counter()
            result = answer(defense, text)
            return result, round((time.perf_counter() - started) * 1000)
        except LLMProviderError as exc:
            if attempt == BUSY_RETRIES:
                raise
            wait = BUSY_WAIT_S if is_panel_busy(exc) else 10
            print(f"    answer failed ({type(exc).__name__}); waiting {wait}s, attempt {attempt}", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def live_answer(live: Path, session: int, question, planned: str) -> str:
    """Hands the question over and waits for the answer written to it."""
    answer_file = live / f"answer-{session}-{question.turn}.txt"
    (live / "question.json").write_text(json.dumps({
        "session": session, "turn": question.turn, "archetype_key": question.panelist.archetype_key,
        "planned": planned, "pages": [question.sources[0].page_start, question.sources[0].page_end],
        "question": question.question, "answer_file": str(answer_file),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"    waiting for {answer_file.name}", flush=True)
    while not answer_file.exists():
        time.sleep(1)
    time.sleep(0.5)  # let the writer finish
    return answer_file.read_text(encoding="utf-8").strip()


def one_session(index: int, matrix, chunks, starts, settings: Settings, embedder, log: ClosingLog,
                plans: list[list[str]] | None = None, live: Path | None = None) -> dict:
    session = Session(
        id=str(uuid.uuid4()),
        panel=PanelConfig(defense_type="thesis", domain=ANICHECK.domain, topic=ANICHECK.topic, archetype_keys=THESIS_PANEL),
        filename=ANICHECK.path.name, last_access=time.monotonic(), status=SessionStatus.READY,
        matrix=matrix, chunks=chunks, chapter_starts=starts,
    )
    calls = CallCounter()
    defense = setup_defense(
        session, gemini_providers_factory(settings)(calls), calls, embedder, settings.gemini_model,
        attribution_notes=settings.attribution_notes, lit_review_access=settings.lit_review_access,
        opening_chapter_filter=settings.opening_chapter_filter, closing_lines="llm",
    )
    seated = [p.archetype_key for p in defense.session.panel if p.archetype_key != DEVILS_ADVOCATE_KEY]
    chosen = plans[index % len(plans)] if plans else PLANS[index % len(PLANS)]
    plan = dict(zip(seated, chosen, strict=True))
    log.lines.clear()
    question = ask_with_retries(defense)
    hand_offs, turns = [], []
    used: dict[str, int] = {}
    while question.panelist.archetype_key != DEVILS_ADVOCATE_KEY:
        key = question.panelist.archetype_key
        texts = ANSWERS[key][plan[key]]
        text = texts[min(used.get(key, 0), len(texts) - 1)]
        if live is not None and (plan[key] == "strong" or (plan[key] == "mid" and used.get(key, 0) >= 2)):
            text = live_answer(live, index + 1, question, plan[key])
        used[key] = used.get(key, 0) + 1
        result, answer_ms = answer_with_retries(defense, text)
        evaluation = result.evaluation
        turns.append({"turn": question.turn, "archetype_key": key, "planned": plan[key], "total": evaluation.total, "answer_ms": answer_ms})
        print(f"    turn {question.turn} {key} planned={plan[key]} total={evaluation.total} ({answer_ms} ms)", flush=True)
        nxt = result.next_question
        assert nxt is not None, "a defense ended before the Terror Panelist spoke"
        if result.closing is not None:
            logged = log.lines.get(question.turn, {})
            hand_offs.append({
                "session": index + 1,
                "turn": question.turn,
                "outgoing": key,
                "outgoing_name": result.closing.panelist.name,
                "outgoing_title": result.closing.panelist.title,
                "question": question.question,
                "answer": text,
                "total": evaluation.total,
                "band": band(evaluation.total),
                "planned": plan[key],
                "answer_summary": evaluation.answer_summary,
                "closing": result.closing.line,
                "source": logged.get("source"),
                "reason": logged.get("reason"),
                "call_ms": None if logged.get("call_ms") in (None, "-") else int(logged["call_ms"]),
                "waited_ms": None if logged.get("waited_ms") is None else int(logged["waited_ms"]),
                "answer_ms": answer_ms,
                "incoming": nxt.panelist.archetype_key,
                "incoming_name": nxt.panelist.name,
                "incoming_question": nxt.question,
            })
            print(f"      closing [{logged.get('source')}/{logged.get('reason')}] waited {logged.get('waited_ms')} ms: {result.closing.line}", flush=True)
        question = nxt
    return {"session": index + 1, "session_id": session.id, "seated": seated, "plan": plan, "turns": turns, "hand_offs": hand_offs, "gemini_calls": calls.total}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--start", type=int, default=0, help="the plan index of the first session, to continue a run")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "v2.0-m15-closing-lines.json")
    parser.add_argument("--plans", help='each session\'s planned bands, e.g. "strong,mid,strong;mid,strong,mid"')
    parser.add_argument("--live", type=Path, help="write the hand-off answers live through this directory")
    args = parser.parse_args()
    if args.sessions > 12:
        raise SystemExit("M15 E1's budget is 12 defenses")
    logging.basicConfig(level=logging.WARNING)
    log = ClosingLog()
    logging.getLogger("backend.closing").addHandler(log)
    logging.getLogger("backend.closing").setLevel(logging.INFO)
    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    matrix, chunks, starts = ingest(ANICHECK.path, embedder)
    print(f"model={settings.gemini_model} closing_lines=llm sessions={args.sessions}", flush=True)
    results = []
    for i in range(args.start, args.start + args.sessions):
        print(f"  session {i + 1}", flush=True)
        plans = [p.split(",") for p in args.plans.split(";")] if args.plans else None
        results.append(one_session(i, matrix, chunks, starts, settings, embedder, log, plans and [plans[(i - args.start) % len(plans)]], args.live))
        print(f"  session {i + 1}: {len(results[-1]['hand_offs'])} hand-offs, {results[-1]['gemini_calls']} Gemini calls", flush=True)
    previous = json.loads(args.out.read_text(encoding="utf-8"))["sessions"] if args.start and args.out.exists() else []
    sessions = previous + results
    hand_offs = [h for s in sessions for h in s["hand_offs"]]
    args.out.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.gemini_model,
        "document": ANICHECK.path.name,
        "panel": THESIS_PANEL,
        "sessions": sessions,
        "hand_offs": len(hand_offs),
        "gemini_calls": sum(s["gemini_calls"] for s in sessions),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(hand_offs)} hand-offs over {len(sessions)} sessions, {sum(s['gemini_calls'] for s in sessions)} Gemini calls -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
