"""End-of-session scoring report — aggregation plus the narrative call. See
`docs/v0.3c-scoring-report-decisions.md` for full rationale. Pure Python aggregate
functions (zero LLM calls) plus one narrative call orchestrated at the end of
`build_report`. No streamlit import; the narrative call goes through the existing
`LLMProvider` abstraction, same as every other LLM call in the engine.

Turn indexing note: `PushbackEvent.turn_index`/`prior_turn_index` are 0-indexed
positions into `session.turns` (and `DefenseReport.difficulty_trajectory`), matching
the decisions doc's `difficulty_trajectory[t] > difficulty_trajectory[t-1]` formula
literally. Turn 1 as printed by the CLI is index 0.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from academic_defense_simulator.digest import turn_total_score
from academic_defense_simulator.engine import STRONG_ANSWER_SUM_THRESHOLD
from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError
from academic_defense_simulator.models.report import (
    DefenseReport,
    PanelistReportSection,
    PushbackEvent,
    PushbackOutcome,
)
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.prompts.panelist_prompts import REPORT_NARRATIVE_PROMPT

logger = logging.getLogger(__name__)

# |quality_sum delta| <= this band -> "held" (Decision 1). Defensible default, not
# evidence-tuned — same epistemic status as the 0.85 grounding threshold at v0.2.5.
HELD_BAND = 1


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
    payload = report.model_dump(mode="json", exclude={"narrative", "narrative_fallback_used"})
    prompt = REPORT_NARRATIVE_PROMPT.format(report_json=json.dumps(payload, indent=2))
    try:
        narrative = provider.generate_text(prompt)
    except LLMProviderError as exc:
        logger.warning("Report narrative call failed after retry — shipping numbers-only report: %s", exc)
        return None, True
    return narrative, False


def build_report(session: DefenseSession, provider: LLMProvider) -> DefenseReport:
    """Composes the pure aggregates, then the narrative step. Called once by the engine
    after the final turn (Decision 5)."""
    overall_avg_clarity, overall_avg_depth, overall_avg_grounding = _overall_averages(session)
    report = DefenseReport(
        difficulty_trajectory=_difficulty_trajectory(session),
        overall_avg_clarity=overall_avg_clarity,
        overall_avg_depth=overall_avg_depth,
        overall_avg_grounding=overall_avg_grounding,
        panelist_sections=_panelist_sections(session),
        pushback_events=_classify_pushback_events(session),
    )
    report.narrative, report.narrative_fallback_used = _generate_narrative(report, provider)
    return report
