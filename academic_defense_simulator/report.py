"""End-of-session scoring report — aggregation plus the narrative call. See
`docs/v0.3c-scoring-report-decisions.md` for full rationale. Pure Python aggregate
functions (zero LLM calls) plus two independent LLM calls orchestrated at the end of
`build_report`: the narrative (v0.3c, flash-lite, numbers-only input, untouched by
this module's v1.0b-2 addition) and answer suggestions (v1.0b-2, gemini-2.5-flash,
fed the real transcript). No streamlit import; both calls go through the existing
`LLMProvider` abstraction, same as every other LLM call in the engine. The two calls
are independent — one failing has no effect on the other (v1.0b-2 Task 4).

Architecturally separate from `analytics.py` (v1.0b): different data flow (one
session's transcript, not many sessions' primary_gap strings), different prompt
module (`answer_suggestions_prompts.py`, not `analytics_prompts.py`), different
version constant (`ANSWER_SUGGESTIONS_PROMPT_VERSION`, never `ANALYTICS_PROMPT_VERSION`
or `PROMPT_VERSION`).

Turn indexing note: `PushbackEvent.turn_index`/`prior_turn_index` and
`AnswerSuggestion.turn_index` are all 0-indexed positions into `session.turns` (and
`DefenseReport.difficulty_trajectory`), matching the decisions doc's
`difficulty_trajectory[t] > difficulty_trajectory[t-1]` formula literally. Turn 1 as
printed by the CLI is index 0.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Optional

from academic_defense_simulator.digest import turn_total_score
from academic_defense_simulator.engine import STRONG_ANSWER_SUM_THRESHOLD
from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError
from academic_defense_simulator.models.report import (
    AnswerSuggestion,
    AnswerSuggestionList,
    DefenseReport,
    PanelistReportSection,
    PushbackEvent,
    PushbackOutcome,
)
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.prompts.answer_suggestions_prompts import ANSWER_SUGGESTIONS_SYSTEM_PROMPT
from academic_defense_simulator.prompts.panelist_prompts import REPORT_NARRATIVE_PROMPT

logger = logging.getLogger(__name__)

# |quality_sum delta| <= this band -> "held" (Decision 1). Defensible default, not
# evidence-tuned — same epistemic status as the 0.85 grounding threshold at v0.2.5.
HELD_BAND = 1


def rescale_score_to_100(score: float) -> int:
    """v1.2.1 Decision 4 — presentation-only 1-5 -> 0-100 rescale, `(x / 5) * 100`
    rounded half up. `AnswerScore` and every persisted/exported value stay 1-5; this
    exists only for rendered report surfaces and the narrative payload below.
    `math.floor(x + 0.5)` rather than the builtin `round` because `round` breaks ties
    to even (round(60.5) == 60), not up."""
    return math.floor(score / 5 * 100 + 0.5)


def _rescaled_narrative_payload(report: DefenseReport) -> dict:
    """v1.2.1 Decision 5 — the narrative call sees the same 100-point numbers the UI
    renders, so its prose can't contradict what's on screen (e.g. narrative says "3.4
    out of 5" under a report showing 68). Averages are rescaled after computation,
    never rescale-then-average. Template wording is untouched — only the payload
    values change."""
    payload = report.model_dump(mode="json", exclude={"narrative", "narrative_fallback_used"})
    payload["overall_avg_clarity"] = rescale_score_to_100(report.overall_avg_clarity)
    payload["overall_avg_depth"] = rescale_score_to_100(report.overall_avg_depth)
    payload["overall_avg_grounding"] = rescale_score_to_100(report.overall_avg_grounding)
    for section, raw in zip(payload["panelist_sections"], report.panelist_sections):
        section["avg_clarity"] = rescale_score_to_100(raw.avg_clarity)
        section["avg_depth"] = rescale_score_to_100(raw.avg_depth)
        section["avg_grounding"] = rescale_score_to_100(raw.avg_grounding)
    return payload


def _difficulty_trajectory(session: DefenseSession) -> list[int]:
    """Per-turn difficulty the candidate faced, in turn order. `difficulty_level` on a
    turn is set from `session.difficulty_current` at question-generation time (engine.py),
    so it already is the difficulty the candidate was asked at, not a post-hoc summary."""
    return [turn.difficulty_level for turn in session.turns]


def _scored_turns(session: DefenseSession) -> list[ConversationTurn]:
    return [turn for turn in session.turns if turn.score is not None]


def _overall_averages(session: DefenseSession) -> tuple[float, float, float]:
    scored = _scored_turns(session)
    if not scored:
        return 0.0, 0.0, 0.0
    n = len(scored)
    return (
        sum(t.score.clarity for t in scored) / n,
        sum(t.score.depth for t in scored) / n,
        sum(t.score.grounding for t in scored) / n,
    )


def _classify_pushback_events(session: DefenseSession) -> list[PushbackEvent]:
    """Per Decision 1: an event at every turn t (t >= 1, 0-indexed) where the trajectory
    escalated. Precedence recovered -> held -> deteriorated. Turns without a score
    (shouldn't happen post-5a) are skipped rather than assumed."""
    events: list[PushbackEvent] = []
    turns = session.turns
    for t in range(1, len(turns)):
        prior_turn, current_turn = turns[t - 1], turns[t]
        if prior_turn.score is None or current_turn.score is None:
            continue
        if current_turn.difficulty_level <= prior_turn.difficulty_level:
            continue

        quality_sum_prior = turn_total_score(prior_turn)
        quality_sum_at = turn_total_score(current_turn)
        delta = quality_sum_at - quality_sum_prior

        if delta > 0 or quality_sum_at >= STRONG_ANSWER_SUM_THRESHOLD:
            outcome = PushbackOutcome.RECOVERED
        elif abs(delta) <= HELD_BAND:
            outcome = PushbackOutcome.HELD
        else:
            outcome = PushbackOutcome.DETERIORATED

        events.append(
            PushbackEvent(
                turn_index=t,
                prior_turn_index=t - 1,
                difficulty_from=prior_turn.difficulty_level,
                difficulty_to=current_turn.difficulty_level,
                quality_sum_prior=quality_sum_prior,
                quality_sum_at=quality_sum_at,
                outcome=outcome,
            )
        )
    return events


def _panelist_sections(session: DefenseSession) -> list[PanelistReportSection]:
    """One section per panelist in `session.panel` composition order — every panelist
    that sat gets a section, even one with zero scored turns (turns_taken=0, averages
    0.0, empty gaps)."""
    sections: list[PanelistReportSection] = []
    for panelist in session.panel:
        panelist_turns = [
            t
            for t in session.turns
            if t.panelist_archetype_key == panelist.archetype_key and t.score is not None
        ]
        n = len(panelist_turns)
        if n == 0:
            sections.append(
                PanelistReportSection(
                    archetype_key=panelist.archetype_key,
                    panelist_name=panelist.panelist_name,
                    turns_taken=0,
                    avg_clarity=0.0,
                    avg_depth=0.0,
                    avg_grounding=0.0,
                    primary_gaps=[],
                )
            )
            continue
        sections.append(
            PanelistReportSection(
                archetype_key=panelist.archetype_key,
                panelist_name=panelist.panelist_name,
                turns_taken=n,
                avg_clarity=sum(t.score.clarity for t in panelist_turns) / n,
                avg_depth=sum(t.score.depth for t in panelist_turns) / n,
                avg_grounding=sum(t.score.grounding for t in panelist_turns) / n,
                primary_gaps=[t.score.primary_gap for t in panelist_turns if t.score.primary_gap is not None],
            )
        )
    return sections


def _generate_narrative(report: DefenseReport, provider: LLMProvider) -> tuple[Optional[str], bool]:
    """One LLM call, numbers in, prose out (Decision 3). On failure (provider already
    retried once internally, per `GeminiProvider.generate_text`'s retry-once-then-raise
    behavior), fall back to a numbers-only report rather than failing the session
    (Decision 4)."""
    payload = _rescaled_narrative_payload(report)
    prompt = REPORT_NARRATIVE_PROMPT.format(report_json=json.dumps(payload, indent=2))
    try:
        narrative = provider.generate_text(prompt)
    except LLMProviderError as exc:
        logger.warning("Report narrative call failed after retry — shipping numbers-only report: %s", exc)
        return None, True
    return narrative, False


def _transcript_payload(session: DefenseSession) -> list[dict]:
    """Every turn's question/answer/grounding_reference/chunk_text, in turn order
    (v1.0b-2 Decision 2's input) — the full transcript, one call, not per-turn."""
    return [
        {
            "turn_index": i,
            "question": turn.question,
            "answer": turn.answer,
            "grounding_reference": turn.grounding_reference,
            "chunk_text": turn.chunk_text,
        }
        for i, turn in enumerate(session.turns)
    ]


def _generate_answer_suggestions(
    session: DefenseSession, provider: LLMProvider
) -> tuple[list[AnswerSuggestion], bool]:
    """One LLM call, the full transcript in, one suggestion per turn out (v1.0b-2
    Decision 2). Fail-open on any API error (same pattern as the narrative call and
    the relevance gate): empty list, fallback flag set, never raises — a suggestions
    failure must never affect the narrative or any other part of the report."""
    prompt = ANSWER_SUGGESTIONS_SYSTEM_PROMPT.format(
        transcript_json=json.dumps(_transcript_payload(session), indent=2)
    )
    try:
        result = provider.generate_structured(prompt, AnswerSuggestionList)
    except LLMProviderError as exc:
        logger.warning("Answer suggestions call failed — shipping without suggestions: %s", exc)
        return [], True
    return result.suggestions, False


def build_report(
    session: DefenseSession, narrative_provider: LLMProvider, suggestions_provider: LLMProvider
) -> DefenseReport:
    """Composes the pure aggregates, then two independent LLM calls: the narrative
    (Decision 5, v0.3c) and the answer suggestions (v1.0b-2 Decision 2). Two separate
    provider parameters because the two calls are pinned to different models (narrative
    stays on whatever GEMINI_MODEL resolves to, per v0.3c; suggestions is pinned to
    gemini-2.5-flash regardless, per v1.0b-2 Decision 2) — same two-provider shape
    `document_relevance.assess_document`'s caller already uses for the gate. Called
    once by the engine after the final turn."""
    overall_avg_clarity, overall_avg_depth, overall_avg_grounding = _overall_averages(session)
    report = DefenseReport(
        difficulty_trajectory=_difficulty_trajectory(session),
        overall_avg_clarity=overall_avg_clarity,
        overall_avg_depth=overall_avg_depth,
        overall_avg_grounding=overall_avg_grounding,
        panelist_sections=_panelist_sections(session),
        pushback_events=_classify_pushback_events(session),
    )
    report.narrative, report.narrative_fallback_used = _generate_narrative(report, narrative_provider)
    report.answer_suggestions, report.suggestions_fallback_used = _generate_answer_suggestions(
        session, suggestions_provider
    )
    return report
