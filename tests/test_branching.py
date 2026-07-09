"""Branching-gate tests (Task 3, item 1): the 11 `_should_follow_up` cases.

These were referenced as "11 unit cases" in the decision docs but never existed in code
(no `tests/` dir, no ad-hoc driver). Written here from the gate's actual logic in
`main.py`: follow up only when the prior answer named a gap AND was not strong AND the
per-chunk follow-up cap has not been hit.
"""

from __future__ import annotations

import pytest

from academic_defense_simulator.main import MAX_FOLLOW_UPS_PER_TOPIC, _should_follow_up
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession


def _score(clarity, depth, grounding, *, gap="sampling justification is thin", delta=1):
    return AnswerScore(
        clarity=clarity, depth=depth, grounding=grounding, difficulty_delta=delta, primary_gap=gap
    )


def _turn(chunk_index, score):
    return ConversationTurn(
        question="q",
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


# Each case: (id, session, expected _should_follow_up result). Follow-up counts are driven
# by how many trailing turns share the last turn's chunk_index (see
# DefenseSession.follow_ups_on_current_topic).
_CASES = [
    ("no-turns-yet", _session(), False),
    ("last-turn-unscored", _session(_turn(5, None)), False),
    ("no-primary-gap-named", _session(_turn(5, _score(2, 2, 2, gap=None))), False),
    ("weak-with-gap-fresh-chunk", _session(_turn(5, _score(1, 1, 1))), True),
    ("weak-with-gap-one-followup-spent", _session(_turn(5, _score(2, 2, 2)), _turn(5, _score(1, 1, 1))), True),
    (
        "weak-with-gap-cap-reached",
        _session(_turn(5, _score(2, 2, 2)), _turn(5, _score(2, 2, 2)), _turn(5, _score(1, 1, 1))),
        False,
    ),
    ("strong-5-4-3-advances", _session(_turn(5, _score(5, 4, 3))), False),
    ("strong-live-5-3-5-pattern", _session(_turn(5, _score(5, 3, 5))), False),
    ("strong-boundary-3-4-4-sum-11", _session(_turn(5, _score(3, 4, 4))), False),
    ("not-strong-low-axis-2-5-5", _session(_turn(5, _score(2, 5, 5))), True),
    ("not-strong-low-sum-3-3-4", _session(_turn(5, _score(3, 3, 4))), True),
]


@pytest.mark.parametrize("session,expected", [(s, e) for _, s, e in _CASES], ids=[c[0] for c in _CASES])
def test_should_follow_up(session, expected):
    assert _should_follow_up(session) is expected


def test_cap_constant_is_two():
    # The cap cases above assume this; pin it so a config change surfaces here.
    assert MAX_FOLLOW_UPS_PER_TOPIC == 2


def test_followups_counted_per_chunk_not_globally():
    # Two turns on chunk 5 then a fresh new-topic turn on chunk 9: the cap counter resets,
    # so a weak answer on the new chunk still triggers a follow-up.
    session = _session(
        _turn(5, _score(2, 2, 2)),
        _turn(5, _score(2, 2, 2)),
        _turn(9, _score(1, 1, 1)),
    )
    assert session.follow_ups_on_current_topic == 0
    assert _should_follow_up(session) is True
