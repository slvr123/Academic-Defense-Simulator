"""Markdown transcript export tests (lean-docs, docs/v1.1a-archetype-expansion-decisions.md).
All zero-LLM — pure-function render over hand-built or fixture `DefenseSession` objects,
same pattern as tests/test_report.py."""

from __future__ import annotations

from academic_defense_simulator.example_session import load_example_session
from academic_defense_simulator.markdown_export import render_session_markdown
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import (
    AnswerSuggestion,
    DefenseReport,
    PanelistReportSection,
    PushbackEvent,
    PushbackOutcome,
)
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession

_PANEL = [
    Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="Exacting about method.", icon="🎓"),
    Panelist(
        archetype_key="literature_theory_specialist",
        panelist_name="Okafor",
        persona_framing="Wants every claim traced to a source.",
        icon="📚",
    ),
]


def _profile(**overrides):
    defaults = dict(
        defense_type=DefenseType.THESIS,
        domain="library science",
        topic="t",
        selected_archetypes=["methodology_expert"],
        document_id="doc-123",
    )
    defaults.update(overrides)
    return DefenseProfile(**defaults)


def _session(*turns, panel=_PANEL, report=None, profile=None):
    return DefenseSession(
        profile=profile or _profile(),
        panel=list(panel),
        difficulty_current=2,
        turns=list(turns),
        report=report,
    )


def _turn(
    archetype_key="methodology_expert",
    name="Reyes",
    difficulty=2,
    clarity=3,
    depth=3,
    grounding=3,
    gap=None,
    chunk_index=0,
    chunk_text="c",
    scored=True,
    answered=True,
):
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
        question="What justifies your sample size?",
        grounding_reference="g",
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        difficulty_level=difficulty,
        answer=("a" if answered else None),
        score=score,
    )


def _report(**overrides):
    defaults = dict(
        difficulty_trajectory=[2, 2, 3],
        overall_avg_clarity=3.0,
        overall_avg_depth=3.0,
        overall_avg_grounding=3.0,
        panelist_sections=[
            PanelistReportSection(
                archetype_key="methodology_expert",
                panelist_name="Reyes",
                turns_taken=2,
                avg_clarity=3.0,
                avg_depth=3.0,
                avg_grounding=3.0,
                primary_gaps=["weak grounding"],
            ),
        ],
        pushback_events=[],
        narrative="The candidate held up well under escalation.",
        narrative_fallback_used=False,
        answer_suggestions=[],
        suggestions_fallback_used=False,
    )
    defaults.update(overrides)
    return DefenseReport(**defaults)


# --- 1. Real fixture, end to end ---


def test_renders_committed_example_session_end_to_end():
    persisted = load_example_session()
    session = persisted.session
    output = render_session_markdown(session, "1.2.2")

    assert isinstance(output, str)
    assert "# Academic Defense Simulator — Session Transcript" in output
    assert f"**Turns:** {len(session.turns)}" in output
    assert "## Panel" in output
    assert "## Transcript" in output
    assert "## Session Report" in output
    assert "https://academic-defense-simulator.streamlit.app/" in output
    for turn in session.turns:
        assert turn.question in output


# --- 2. Redaction ---


def test_chunk_text_never_appears_in_output():
    sentinel = "SENTINEL-DOCUMENT-CHUNK-TEXT-MUST-NOT-LEAK-8f21"
    turn = _turn(chunk_text=sentinel)
    session = _session(turn, report=_report(difficulty_trajectory=[2]))
    output = render_session_markdown(session, "1.2.2")
    assert sentinel not in output


# --- 3. D7 degraded cases ---


def test_aborted_session_no_report_renders_without_raising():
    turn = _turn()
    session = _session(turn, report=None)
    output = render_session_markdown(session, "1.2.2")
    assert "No report available — this session did not reach completion." in output
    assert turn.question in output


def test_missing_suggestion_renders_without_raising():
    turn = _turn(gap="weak grounding")
    report = _report(difficulty_trajectory=[2], answer_suggestions=[])
    session = _session(turn, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "Suggested improvement" not in output


def test_unanswered_final_turn_renders_without_raising():
    answered_turn = _turn()
    unanswered_turn = _turn(answered=False, scored=False)
    report = _report(difficulty_trajectory=[2, 2])
    session = _session(answered_turn, unanswered_turn, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "*(unanswered)*" in output
    assert "*(not scored)*" in output


def test_flat_difficulty_trajectory_renders_without_raising():
    turns = [_turn(difficulty=2), _turn(difficulty=2), _turn(difficulty=2)]
    report = _report(difficulty_trajectory=[2, 2, 2], pushback_events=[])
    session = _session(*turns, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "**Difficulty trajectory:** 2 → 2 → 2" in output
    assert "No escalation moments this session." in output


def test_suggestion_present_for_matching_turn_index():
    turn = _turn()
    suggestion = AnswerSuggestion(turn_index=0, suggestion="Cite the sample-size justification directly.", grounding_reference="g")
    report = _report(difficulty_trajectory=[2], answer_suggestions=[suggestion])
    session = _session(turn, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "**Suggested improvement:** Cite the sample-size justification directly." in output


def test_suggestions_fallback_used_omits_all_suggestions():
    turn = _turn()
    suggestion = AnswerSuggestion(turn_index=0, suggestion="Should not appear.", grounding_reference="g")
    report = _report(difficulty_trajectory=[2], answer_suggestions=[suggestion], suggestions_fallback_used=True)
    session = _session(turn, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "Should not appear." not in output


def test_pushback_events_render_as_table():
    turn1 = _turn(difficulty=2, clarity=2, depth=2, grounding=2)
    turn2 = _turn(difficulty=3, clarity=4, depth=4, grounding=4)
    report = _report(
        difficulty_trajectory=[2, 3],
        pushback_events=[
            PushbackEvent(
                turn_index=1,
                prior_turn_index=0,
                difficulty_from=2,
                difficulty_to=3,
                quality_sum_prior=6,
                quality_sum_at=12,
                outcome=PushbackOutcome.RECOVERED,
            )
        ],
    )
    session = _session(turn1, turn2, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "| 2 | 2 → 3 | recovered |" in output


def test_narrative_fallback_used_shows_placeholder():
    turn = _turn()
    report = _report(difficulty_trajectory=[2], narrative="should not appear", narrative_fallback_used=True)
    session = _session(turn, report=report)
    output = render_session_markdown(session, "1.2.2")
    assert "*Narrative unavailable for this session.*" in output
    assert "should not appear" not in output


def test_other_defense_type_includes_subtype():
    from academic_defense_simulator.models.defense_profile import OtherSubtype

    profile = _profile(
        defense_type=DefenseType.OTHER,
        other_subtype=OtherSubtype.ORAL_COMPS,
        selected_archetypes=["methodology_expert"],
    )
    turn = _turn()
    session = _session(turn, report=_report(difficulty_trajectory=[2]), profile=profile)
    output = render_session_markdown(session, "1.2.2")
    assert "Other / Oral Comps" in output


# --- 4. Determinism ---


def test_same_session_renders_identical_string_twice():
    turn = _turn(gap="weak grounding")
    report = _report(difficulty_trajectory=[2])
    session = _session(turn, report=report)
    first = render_session_markdown(session, "1.2.2")
    second = render_session_markdown(session, "1.2.2")
    assert first == second
