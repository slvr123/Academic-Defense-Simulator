"""Standalone diagnostic: compare AnswerScore behavior across two Gemini models.

Scores fixed answer variants against the same question + grounding chunk on
gemini-2.5-flash and gemini-3.1-flash-lite, repeated per an asymmetric budget-aware
run plan, then prints per-response JSON and a raw aggregate table. No verdict is
emitted — interpretation happens outside the script, on the raw tabulated data, to
keep to the project's evidence standard (raw output, not a self-generated summary).

This run isolates tone from substance: weak_hedging and weak_confident share the same
underlying failure (never justifying why 20 is adequate for the five-criteria claim)
but differ only in phrasing — hedging vs. confident technical prose. Divergent scores
across the two under matched substance implicate tone-tracking, not engagement.

Does NOT modify main.py, the session models, or any prompt template — it reuses the
existing SCORING_SYSTEM_PROMPT, AnswerScore, and GeminiProvider unchanged.

Run: python scripts/compare_scoring_models.py
Raw responses are also appended to scripts/scoring_comparison_results.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, SCORING_SYSTEM_PROMPT

# --- Fixed panelist context (matches Day 2/3 verification runs) -----------------
BASELINE_MODEL = "gemini-2.5-flash"
CANDIDATE_MODEL = "gemini-3.1-flash-lite"  # stable ID, confirmed from Google's Gemini API docs (not the AI Studio display name)

DEFENSE_TYPE = "thesis"
PANELIST_NAME = "Reyes"
ACTIVE_ARCHETYPE = "methodology_expert"

RESULTS_PATH = Path(__file__).resolve().parent / "scoring_comparison_results.jsonl"

# Free-tier RPM differs per model: 2.5-flash is ~5 RPM, flash-lite ~15 RPM. Pace by the
# model actually being called so a 12-call flash-lite burst can't trip 429, without
# over-waiting on it. (~4s is the 15-RPM floor; 5s leaves margin.)
PACING_SECONDS = {BASELINE_MODEL: 13, CANDIDATE_MODEL: 5}

# --- Fixed test inputs (identical grounding for every call -> apples-to-apples) --
QUESTION = (
    "Given the study only surveyed 20 respondents, how can this sample be considered "
    "adequate to support a 'holistic evaluation' claim spanning five criteria "
    "(functionality, usability, reliability, security, effectiveness)?"
)

# The purposive-sampling section retrieved as chunk 52 in Day 2/3 verification.
# Anchored on the verbatim phrases captured in docs/v0.1-skeleton-verification.md / docs/v0.2-agent-loop-verification.md.
GROUNDING_CHUNK = (
    "Purposive sampling was used to select the evaluators for the system. The sample "
    "will include 20 respondents, who will be split into two categories: 10 IT-related "
    "professionals and 10 non-IT respondents. This split will guarantee that both the "
    "technical and practical views are captured. Each respondent evaluated the system "
    "using a Likert-scale instrument covering the quality criteria of functionality, "
    "usability, reliability, security, and effectiveness."
)

# weak_hedging and weak_confident share the SAME substance failure — neither ever
# justifies why 20 is adequate for the five-criteria claim; they only differ in tone.
WEAK_HEDGING = (
    "We used 20 respondents because that's what our adviser suggested and it was easier "
    "to gather data with a small group. We believe it's enough since the results all "
    "pointed in the same direction anyway."
)

WEAK_CONFIDENT = (
    "The sample size of 20 was determined through purposive sampling methodology, which "
    "is a well-established qualitative approach that prioritizes information richness over "
    "statistical generalizability. Given the exploratory nature of this evaluation, this "
    "sample provides sufficient depth for the holistic assessment we conducted."
)

STRONG_ANSWER = (
    "We acknowledge 20 is a small sample and explicitly frame this as a pilot-scale "
    "evaluation rather than a generalizable claim. The purposive split of 10 IT and 10 "
    "non-IT respondents was chosen to capture both technical and end-user perspectives "
    "on each of the five criteria, and the Likert instrument was the same validated "
    "format used in comparable capstone evaluations. We note this as a limitation in our "
    "conclusions section and recommend a larger follow-up study before generalizing the "
    "findings."
)

# --- Probe-plan answers: engineered to force difficulty_delta OFF +1 if the model
# can move it at all. Ground truth from the Day 3 live session (2.5-flash):
#   a total non-answer -> -1 (ease up); an honest gap-acknowledgment -> 0 (hold).
# If flash-lite still returns +1 on these, its difficulty_delta is genuinely saturated.
NONANSWER = (
    "Honestly, I don't really remember the exact reasoning behind the sample size. "
    "I'd have to go back and check my notes — I'm not sure how to answer that right now."
)

PARTIAL_ACKNOWLEDGMENT = (
    "That's a fair point, and I think you're right that 20 is a weak spot in our study. "
    "I can't fully defend it as adequate for all five criteria — I'd honestly frame it "
    "now as a limitation and something a larger follow-up study should address."
)

ANSWER_TEXTS = {
    "weak_hedging": WEAK_HEDGING,
    "weak_confident": WEAK_CONFIDENT,
    "strong": STRONG_ANSWER,
    "nonanswer": NONANSWER,
    "partial": PARTIAL_ACKNOWLEDGMENT,
}

# Run plans: (model, answer_variant, reps). Selected via --plan.
#
# "full": asymmetric, budget-aware tone-vs-substance sweep.
#   flash-lite is cheap (500 RPD) -> repeat to see whether phrasing moves its scores.
#   2.5-flash quota is scarce -> only the one cell not yet established (weak_confident);
#   weak_hedging and strong on 2.5-flash are already on record from Day 3 + prior run.
FULL_PLAN = [
    (CANDIDATE_MODEL, "weak_hedging", 5),
    (CANDIDATE_MODEL, "weak_confident", 5),
    (CANDIDATE_MODEL, "strong", 2),
    (BASELINE_MODEL, "weak_confident", 1),
]

# "probe": does flash-lite's difficulty_delta EVER leave +1? All flash-lite (no scarce
# 2.5-flash quota). nonanswer should ease down (-1), partial should hold (0), strong
# re-confirms +1 escalation. 2.5-flash's -1/0 on these scenarios is already on record
# from the Day 3 live session, so it isn't re-run here.
PROBE_PLAN = [
    (CANDIDATE_MODEL, "nonanswer", 3),
    (CANDIDATE_MODEL, "partial", 3),
    (CANDIDATE_MODEL, "strong", 2),
]

PLANS = {"full": FULL_PLAN, "probe": PROBE_PLAN}


@dataclass(frozen=True)
class Result:
    model: str
    answer_variant: str
    rep: int
    score: AnswerScore | None
    error: str | None = None


def _score(model: str, answer: str, api_key: str) -> AnswerScore:
    """Score one answer via the existing provider, parameterized by model name."""
    archetype = ARCHETYPE_CONFIG[ACTIVE_ARCHETYPE]
    provider = GeminiProvider(api_key=api_key, model=model)
    prompt = SCORING_SYSTEM_PROMPT.format(
        defense_type=DEFENSE_TYPE,
        panelist_name=PANELIST_NAME,
        archetype_title=archetype["archetype_title"],
        question=QUESTION,
        answer=answer,
        retrieved_chunk=GROUNDING_CHUNK,
    )
    return provider.generate_structured(prompt, AnswerScore)


def _expand_plan(plan: list[tuple[str, str, int]]) -> list[tuple[str, str, int, int]]:
    """Flatten a plan into individual (model, variant, rep, reps) calls."""
    calls: list[tuple[str, str, int, int]] = []
    for model, variant, reps in plan:
        for rep in range(1, reps + 1):
            calls.append((model, variant, rep, reps))
    return calls


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_deltas(deltas: list[int]) -> str:
    """e.g. [1,1,1,1,-1] -> '+1 ×4, -1 ×1' (most frequent first)."""
    counts = Counter(deltas)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))
    return ", ".join(f"{d:+d} ×{c}" for d, c in ordered)


def _mean(values: list[int]) -> str:
    return f"{sum(values) / len(values):.2f}" if values else "—"


def _print_aggregate(results: list[Result]) -> None:
    # Group by (model, variant), preserving first-seen order.
    groups: "OrderedDict[tuple[str, str], list[Result]]" = OrderedDict()
    for r in results:
        groups.setdefault((r.model, r.answer_variant), []).append(r)

    headers = ["model", "answer_variant", "n", "difficulty_delta", "mean clarity", "mean depth", "mean grounding"]
    rows: list[list[str]] = []
    gap_listing: list[tuple[str, str, list[str]]] = []

    for (model, variant), group in groups.items():
        scored = [r.score for r in group if r.score is not None]
        errors = [r for r in group if r.score is None]
        n = len(scored)

        rows.append(
            [
                model,
                variant,
                str(n) + (f" (+{len(errors)} err)" if errors else ""),
                _fmt_deltas([s.difficulty_delta for s in scored]) if scored else "—",
                _mean([s.clarity for s in scored]),
                _mean([s.depth for s in scored]),
                _mean([s.grounding for s in scored]),
            ]
        )

        # Distinct primary_gap strings, full wording, first-seen order (the wording matters).
        distinct: list[str] = []
        for s in scored:
            g = "None" if s.primary_gap is None else s.primary_gap
            if g not in distinct:
                distinct.append(g)
        for r in errors:
            tag = f"[ERROR] {r.error}"
            if tag not in distinct:
                distinct.append(tag)
        gap_listing.append((model, variant, distinct))

    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))

    # primary_gap strings can't fit an aligned column at full width, so list them below
    # the numeric table rather than truncating — per the brief, the actual wording matters.
    print("\n=== distinct primary_gap strings seen (full wording) ===")
    for model, variant, distinct in gap_listing:
        print(f"\n{model} / {variant}:")
        for g in distinct:
            print(f"  - {g}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare AnswerScore behavior across Gemini models.")
    parser.add_argument(
        "--plan",
        choices=sorted(PLANS),
        default="probe",
        help="Which run plan to execute: 'full' (tone-vs-substance sweep) or "
        "'probe' (flash-lite difficulty_delta saturation test). Default: probe.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    plan = PLANS[args.plan]
    settings = load_settings()

    print(f"=== Scoring-model comparison (plan: {args.plan}) ===")
    print(f"  baseline : {BASELINE_MODEL}")
    print(f"  candidate: {CANDIDATE_MODEL}")
    print(f"  panelist : Dr. {PANELIST_NAME} ({ARCHETYPE_CONFIG[ACTIVE_ARCHETYPE]['archetype_title']}), {DEFENSE_TYPE} defense")
    print(f"  raw log  : {RESULTS_PATH}")
    print(f"\nQuestion:\n  {QUESTION}\n")

    calls = _expand_plan(plan)
    results: list[Result] = []

    # Append + flush each record so prior runs' data survives and a mid-run quota hit
    # still leaves every completed response on disk. run_label distinguishes runs.
    run_label = f"{args.plan}@{_now_iso()}"
    with RESULTS_PATH.open("a", encoding="utf-8") as log:
        for i, (model, variant, rep, reps) in enumerate(calls):
            print(f"[{i + 1}/{len(calls)}] {variant} rep {rep}/{reps} on {model} ...", flush=True)
            record: dict = {
                "run_label": run_label,
                "timestamp": _now_iso(),
                "model": model,
                "answer_variant": variant,
                "rep": rep,
            }
            try:
                score = _score(model, ANSWER_TEXTS[variant], settings.gemini_api_key.get())
                results.append(Result(model=model, answer_variant=variant, rep=rep, score=score))
                record["score"] = score.model_dump()
                record["error"] = None
                print(f"    -> {score.model_dump_json()}")
            except Exception as exc:  # keep one bad call from sinking the whole run
                err = f"{type(exc).__name__}: {exc}"
                results.append(Result(model=model, answer_variant=variant, rep=rep, score=None, error=err))
                record["score"] = None
                record["error"] = err
                print(f"    -> FAILED: {err}")

            log.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.flush()

            if i < len(calls) - 1:
                time.sleep(PACING_SECONDS[model])

    print("\n=== Aggregate ===")
    _print_aggregate(results)


if __name__ == "__main__":
    main()
