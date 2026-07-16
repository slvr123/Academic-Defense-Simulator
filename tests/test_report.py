"""Report aggregation tests (v0.3c Task 4). All pure/mocked — no network, no PDFs, no
embedding model. Synthetic `DefenseSession` fixtures built by hand, same pattern as
tests/test_engine.py and tests/test_panel.py's stub providers."""

from __future__ import annotations

import logging

from academic_defense_simulator.engine import STRONG_ANSWER_SUM_THRESHOLD, _is_strong_answer
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import PushbackOutcome
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.report import (
    HELD_BAND,
    _classify_pushback_events,
    _difficulty_trajectory,
    _overall_averages,
    _panelist_sections,
    build_report,
)

_PANEL = [
    Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f"),
    Panelist(archetype_key="literature_theory_specialist", panelist_name="Okafor", persona_framing="f"),
]


def _profile():
    return DefenseProfile(
        defense_type=DefenseType.THESIS,
        domain="library science",
        topic="t",
        selected_archetypes=["methodology_expert"],
        document_id="doc",
    )


def _session(*turns, panel=_PANEL):
    return DefenseSession(profile=_profile(), panel=list(panel), difficulty_current=2, turns=list(turns))


def _turn(archetype_key, name, difficulty, clarity, depth, grounding, gap=None, chunk_index=0, scored=True):
    score = (
        AnswerScore(
            clarity=clarity, depth=depth, grounding=grounding, difficulty_delta=0, primary_gap=gap, answer_summary="s"
        )
        if scored
        else None
    )
    return ConversationTurn(
        panelist_archetype_key=archetype_key,
        panelist_name=name,
        question="q",
        grounding_reference="g",
        chunk_index=chunk_index,
        chunk_text="c",
        difficulty_level=difficulty,
        answer="a" if scored else None,
        score=score,
    )


# --- 1. Trajectory extraction ---


def test_trajectory_extraction_matches_per_turn_difficulty_in_order():
    turns = [
        _turn("methodology_expert", "Reyes", 2, 3, 3, 3),
        _turn("methodology_expert", "Reyes", 2, 3, 3, 3),
        _turn("methodology_expert", "Reyes", 3, 4, 4, 3),
        _turn("methodology_expert", "Reyes", 4, 4, 4, 4),
    ]
    session = _session(*turns)
    assert _difficulty_trajectory(session) == [2, 2, 3, 4]


# --- 2. Pushback classification ---


def test_recovered_via_improved_sum():
    prior = _turn("methodology_expert", "Reyes", 2, 2, 2, 2)  # sum 6
    current = _turn("methodology_expert", "Reyes", 3, 3, 3, 3)  # sum 9, escalated, improved
    session = _session(prior, current)
    events = _classify_pushback_events(session)
    assert len(events) == 1
    assert events[0].outcome == PushbackOutcome.RECOVERED
    assert events[0].quality_sum_prior == 6
    assert events[0].quality_sum_at == 9


def test_held_via_zero_delta():
    prior = _turn("methodology_expert", "Reyes", 2, 3, 3, 3)  # sum 9
    current = _turn("methodology_expert", "Reyes", 3, 3, 3, 3)  # sum 9, escalated, delta 0
    session = _session(prior, current)
    events = _classify_pushback_events(session)
    assert events[0].outcome == PushbackOutcome.HELD


def test_deteriorated_via_large_negative_delta():
    prior = _turn("methodology_expert", "Reyes", 2, 4, 3, 3)  # sum 10
    current = _turn("methodology_expert", "Reyes", 3, 3, 3, 2)  # sum 8, escalated, delta -2
    session = _session(prior, current)
    events = _classify_pushback_events(session)
    assert events[0].outcome == PushbackOutcome.DETERIORATED


def test_recovered_precedence_over_dip_when_sum_at_meets_strong_threshold():
    """Sum dipped (delta < -1, would otherwise be 'deteriorated') but sum_at still meets
    the strong-answer threshold -> recovered wins on precedence (Decision 1)."""
    prior = _turn("methodology_expert", "Reyes", 2, 5, 5, 4)  # sum 14
    current = _turn("methodology_expert", "Reyes", 3, 4, 4, 3)  # sum 11, escalated, delta -3
    session = _session(prior, current)
    assert current.score.clarity + current.score.depth + current.score.grounding == STRONG_ANSWER_SUM_THRESHOLD
    events = _classify_pushback_events(session)
    assert events[0].outcome == PushbackOutcome.RECOVERED


def test_zero_escalations_yields_empty_list_no_placeholder():
    turns = [
        _turn("methodology_expert", "Reyes", 3, 3, 3, 3),
        _turn("methodology_expert", "Reyes", 3, 2, 2, 2),  # held/flat, no escalation
        _turn("methodology_expert", "Reyes", 2, 4, 4, 4),  # de-escalation
    ]
    session = _session(*turns)
    assert _classify_pushback_events(session) == []


def test_boundary_delta_exactly_minus_one_is_held():
    prior = _turn("methodology_expert", "Reyes", 2, 4, 3, 3)  # sum 10
    current = _turn("methodology_expert", "Reyes", 3, 3, 3, 3)  # sum 9, escalated, delta -1
    session = _session(prior, current)
    events = _classify_pushback_events(session)
    assert abs(events[0].quality_sum_at - events[0].quality_sum_prior) == HELD_BAND
    assert events[0].outcome == PushbackOutcome.HELD


def test_boundary_delta_exactly_minus_two_is_deteriorated():
    prior = _turn("methodology_expert", "Reyes", 2, 4, 3, 3)  # sum 10
    current = _turn("methodology_expert", "Reyes", 3, 3, 3, 2)  # sum 8, escalated, delta -2
    session = _session(prior, current)
    events = _classify_pushback_events(session)
    assert events[0].quality_sum_prior - events[0].quality_sum_at == 2
    assert events[0].outcome == PushbackOutcome.DETERIORATED


# --- 3. Per-panelist attribution ---


def test_per_panelist_attribution_averages_gaps_and_zero_turn_panelist():
    turns = [
        _turn("methodology_expert", "Reyes", 2, 4, 4, 2, gap="weak grounding"),
        _turn("methodology_expert", "Reyes", 3, 2, 2, 2, gap=None),  # no material gap
        _turn("methodology_expert", "Reyes", 3, 3, 3, 3, gap="unjustified sample size"),
    ]
    session = _session(*turns, panel=_PANEL)  # literature_theory_specialist never speaks

    sections = _panelist_sections(session)
    assert [s.archetype_key for s in sections] == ["methodology_expert", "literature_theory_specialist"]

    methodology = sections[0]
    assert methodology.turns_taken == 3
    assert methodology.avg_clarity == (4 + 2 + 3) / 3
    assert methodology.avg_depth == (4 + 2 + 3) / 3
    assert methodology.avg_grounding == (2 + 2 + 3) / 3
    assert methodology.primary_gaps == ["weak grounding", "unjustified sample size"]  # in order, nulls excluded

    literature = sections[1]
    assert literature.turns_taken == 0
    assert literature.avg_clarity == 0.0
    assert literature.avg_depth == 0.0
    assert literature.avg_grounding == 0.0
    assert literature.primary_gaps == []


# --- 4. Averages, including unscored-turn exclusion ---


def test_overall_averages_hand_computed_and_exclude_unscored_turn():
    turns = [
        _turn("methodology_expert", "Reyes", 2, 4, 4, 4),
        _turn("methodology_expert", "Reyes", 3, 2, 2, 2),
        _turn("methodology_expert", "Reyes", 3, 0, 0, 0, scored=False),  # unscored, excluded
    ]
    session = _session(*turns)

    avg_clarity, avg_depth, avg_grounding = _overall_averages(session)
    assert avg_clarity == (4 + 2) / 2
    assert avg_depth == (4 + 2) / 2
    assert avg_grounding == (4 + 2) / 2

    # Trajectory still records every turn's faced difficulty, scored or not.
    assert _difficulty_trajectory(session) == [2, 3, 3]

    # Unscored turn also excluded from that panelist's section averages/turns_taken.
    sections = _panelist_sections(session)
    assert sections[0].turns_taken == 2


# --- 5. Narrative fallback ---


class _StubTextProvider:
    """Mocked LLMProvider.generate_text — models the net effect observed at report.py's
    boundary: GeminiProvider.generate_text already retries once internally before ever
    raising LLMProviderError (Decision 3: 'wrap at the provider boundary'), so report.py
    only ever makes one call and sees either a string or a final LLMProviderError."""

    def __init__(self, response):
        self._response = response
        self.calls = 0

    def generate_structured(self, prompt, response_model):
        raise NotImplementedError

    def generate_text(self, prompt):
        self.calls += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_narrative_fallback_when_provider_fails():
    provider = _StubTextProvider(LLMProviderError("Gemini API request timed out — retried once and failed again."))
    session = _session(_turn("methodology_expert", "Reyes", 2, 3, 3, 3))

    report = build_report(session, provider)

    assert provider.calls == 1  # report.py makes exactly one call; the retry is embedded upstream
    assert report.narrative is None
    assert report.narrative_fallback_used is True


def test_narrative_fallback_logs_warning(caplog):
    provider = _StubTextProvider(LLMProviderError("forced failure"))
    session = _session(_turn("methodology_expert", "Reyes", 2, 3, 3, 3))
    with caplog.at_level(logging.WARNING):
        build_report(session, provider)
    assert "numbers-only report" in caplog.text


def test_narrative_populated_when_provider_succeeds():
    provider = _StubTextProvider("You defended your methodology clearly under pressure.")
    session = _session(_turn("methodology_expert", "Reyes", 2, 3, 3, 3))

    report = build_report(session, provider)

    assert provider.calls == 1
    assert report.narrative == "You defended your methodology clearly under pressure."
    assert report.narrative_fallback_used is False


# --- 6. Strong-threshold reuse (pins the "no second threshold" decision) ---


def test_pushback_classification_reuses_should_follow_ups_strong_threshold():
    """Same constant, not a re-declared literal (Decision 1). Confirms both the imported
    name and that _is_strong_answer's own sum floor is that same constant."""
    strong = AnswerScore(
        clarity=4, depth=4, grounding=3, difficulty_delta=0, primary_gap=None, answer_summary="s"
    )  # sum == STRONG_ANSWER_SUM_THRESHOLD exactly
    assert strong.clarity + strong.depth + strong.grounding == STRONG_ANSWER_SUM_THRESHOLD
    assert _is_strong_answer(strong) is True

    prior = _turn("methodology_expert", "Reyes", 2, 5, 5, 5)  # sum 15, dips hard
    current = _turn("methodology_expert", "Reyes", 3, 4, 4, 3)  # sum 11 == STRONG_ANSWER_SUM_THRESHOLD
    session = _session(prior, current)
    events = _classify_pushback_events(session)
    assert events[0].quality_sum_at == STRONG_ANSWER_SUM_THRESHOLD
    assert events[0].outcome == PushbackOutcome.RECOVERED
