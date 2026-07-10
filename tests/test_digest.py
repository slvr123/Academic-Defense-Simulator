"""Digest renderer tests (v0.3b Task 2): render-only, zero LLM calls, capped at N."""

from __future__ import annotations

import re

from academic_defense_simulator.digest import DIGEST_MAX_TURNS, render_digest_block, turn_total_score
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession


def _score(clarity=3, depth=3, grounding=3, *, gap="a gap", summary="a claim"):
    return AnswerScore(
        clarity=clarity, depth=depth, grounding=grounding, difficulty_delta=0, primary_gap=gap, answer_summary=summary
    )


def _turn(archetype_key, name, chunk_index, score, question="q"):
    return ConversationTurn(
        panelist_archetype_key=archetype_key,
        panelist_name=name,
        question=question,
        grounding_reference="g",
        chunk_index=chunk_index,
        chunk_text="c",
        difficulty_level=2,
        answer="a",
        score=score,
    )


def _session(*turns):
    profile = DefenseProfile(
        defense_type=DefenseType.THESIS, domain="library science", topic="t", document_id="doc"
    )
    return DefenseSession(profile=profile, panel=[], difficulty_current=2, turns=list(turns))


def test_turn_total_score_sums_subscores():
    assert turn_total_score(_turn("methodology_expert", "Reyes", 1, _score(4, 3, 2))) == 9


def test_empty_on_no_turns():
    assert render_digest_block(_session()) == ""


def test_empty_when_only_turn_is_unscored():
    unscored = ConversationTurn(
        panelist_archetype_key="methodology_expert",
        panelist_name="Reyes",
        question="q",
        grounding_reference="g",
        chunk_index=1,
        chunk_text="c",
        difficulty_level=2,
    )
    assert render_digest_block(_session(unscored)) == ""


def test_renders_required_fields_for_scored_turn():
    turn = _turn("methodology_expert", "Reyes", 5, _score(4, 4, 4, gap="thin justification", summary="claimed X"))
    block = render_digest_block(_session(turn))
    assert "methodology_expert" in block
    assert "Reyes" in block
    assert "q" in block
    assert "claimed X" in block
    assert "thin justification" in block
    assert "12/15" in block  # 4+4+4


def test_chronological_order_preserved():
    t1 = _turn("methodology_expert", "Reyes", 1, _score(), question="first question")
    t2 = _turn("literature_theory_specialist", "Okafor", 2, _score(), question="second question")
    block = render_digest_block(_session(t1, t2))
    assert block.index("first question") < block.index("second question")


def test_capped_at_max_turns_keeps_exactly_the_last_n_in_order():
    """Off-by-one guard: drives a session past the N-turn cap (a real defense will
    eventually do this) and asserts the digest holds exactly the last N turns, in the
    same chronological order they occurred, with the oldest turns dropped — not just
    that truncation happened at all."""
    total = DIGEST_MAX_TURNS + 2
    turns = [_turn("methodology_expert", "Reyes", i, _score(), question=f"question {i}") for i in range(total)]
    block = render_digest_block(_session(*turns), max_turns=DIGEST_MAX_TURNS)

    surviving_question_numbers = [int(n) for n in re.findall(r"question (\d+)", block)]
    expected = list(range(total - DIGEST_MAX_TURNS, total))  # last N indices, oldest-to-newest
    assert surviving_question_numbers == expected
    assert block.count("panelist_id:") == DIGEST_MAX_TURNS
