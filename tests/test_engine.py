"""Engine tests (v0.3b Tasks 3-4): round-robin turn-taking with DA last, and Devil's
Advocate's deterministic target selection. No network — provider is a stub, same
pattern as tests/test_panel.py's _StubProvider."""

from __future__ import annotations

import logging

import pytest

import academic_defense_simulator.engine as engine_module
from academic_defense_simulator.engine import (
    MAX_FOLLOW_UPS_PER_TOPIC,
    _generate_da_question,
    _generate_question,
    _should_follow_up,
    select_active_panelist,
    session_is_complete,
    t_max,
)
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.prompts.panelist_prompts import HIGH_DIFFICULTY_GROUNDING_GUARD
from academic_defense_simulator.rag.retrieval import Chunk

_PANEL = [
    Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓"),
    Panelist(archetype_key="literature_theory_specialist", panelist_name="Okafor", persona_framing="f", icon="🎓"),
    Panelist(archetype_key="ethics_practicality_reviewer", panelist_name="Alvarez", persona_framing="f", icon="🎓"),
    Panelist(archetype_key=DEVILS_ADVOCATE_KEY, panelist_name="Marlowe", persona_framing="f", icon="🎓"),
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


def _score(clarity, depth, grounding, *, gap="a gap", summary="a claim"):
    return AnswerScore(
        clarity=clarity, depth=depth, grounding=grounding, difficulty_delta=0, primary_gap=gap, answer_summary=summary
    )


def _turn(archetype_key, name, chunk_index, chunk_text, score, question="q"):
    return ConversationTurn(
        panelist_archetype_key=archetype_key,
        panelist_name=name,
        question=question,
        grounding_reference="g",
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        difficulty_level=2,
        answer="a",
        score=score,
    )


# --- select_active_panelist (v0.3f Decision 3, replacing round-robin) ---


def test_follow_up_returns_the_same_panelist():
    weak = _turn("methodology_expert", "Reyes", 0, "c0", _score(2, 2, 2))
    session = _session(weak)
    assert select_active_panelist(session).archetype_key == "methodology_expert"


def test_new_topic_rotation_advances_among_unspoken_domain_panelists():
    m_strong = _turn("methodology_expert", "Reyes", 0, "c0", _score(5, 5, 5))
    session = _session(m_strong)
    assert select_active_panelist(session).archetype_key == "literature_theory_specialist"

    l_strong = _turn("literature_theory_specialist", "Okafor", 1, "c1", _score(5, 5, 5))
    session = _session(m_strong, l_strong)
    assert select_active_panelist(session).archetype_key == "ethics_practicality_reviewer"


def test_devils_advocate_triggers_exactly_once_after_all_domain_panelists_spoken():
    m = _turn("methodology_expert", "Reyes", 0, "c0", _score(5, 5, 5))
    l = _turn("literature_theory_specialist", "Okafor", 1, "c1", _score(5, 5, 5))
    e = _turn("ethics_practicality_reviewer", "Alvarez", 2, "c2", _score(5, 5, 5))

    # Before all three domain panelists have spoken, DA is never selected.
    assert select_active_panelist(_session(m, l)).archetype_key == "ethics_practicality_reviewer"

    session = _session(m, l, e)
    assert select_active_panelist(session).archetype_key == DEVILS_ADVOCATE_KEY


def test_devils_advocates_own_follow_up_stays_with_devils_advocate():
    m = _turn("methodology_expert", "Reyes", 0, "c0", _score(5, 5, 5))
    l = _turn("literature_theory_specialist", "Okafor", 1, "c1", _score(5, 5, 5))
    e = _turn("ethics_practicality_reviewer", "Alvarez", 2, "c2", _score(5, 5, 5))
    da_weak = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 1, "c1", _score(2, 2, 2))
    session = _session(m, l, e, da_weak)
    assert select_active_panelist(session).archetype_key == DEVILS_ADVOCATE_KEY


# --- session_is_complete / t_max (v0.3f Decisions 5 and 6) ---


def test_session_not_complete_before_devils_advocate_speaks():
    m = _turn("methodology_expert", "Reyes", 0, "c0", _score(5, 5, 5))
    l = _turn("literature_theory_specialist", "Okafor", 1, "c1", _score(5, 5, 5))
    e = _turn("ethics_practicality_reviewer", "Alvarez", 2, "c2", _score(5, 5, 5))
    assert session_is_complete(_session(m, l, e)) is False


def test_shortest_case_all_strong_answers_ends_right_after_devils_advocate():
    m = _turn("methodology_expert", "Reyes", 0, "c0", _score(5, 5, 5))
    l = _turn("literature_theory_specialist", "Okafor", 1, "c1", _score(5, 5, 5))
    e = _turn("ethics_practicality_reviewer", "Alvarez", 2, "c2", _score(5, 5, 5))
    da_strong = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 1, "c1", _score(5, 5, 5))
    session = _session(m, l, e, da_strong)
    assert session_is_complete(session) is True
    assert len(session.turns) == 4  # best case: one turn per panelist, no follow-ups


def test_longest_case_all_max_follow_ups_reaches_t_max_exactly():
    turns: list = []
    # M, then L, then E: initial + 2 follow-ups each, all weak, distinct chunks.
    for archetype_key, name, chunk_index in [
        ("methodology_expert", "Reyes", 0),
        ("literature_theory_specialist", "Okafor", 1),
        ("ethics_practicality_reviewer", "Alvarez", 2),
    ]:
        for _ in range(1 + 2):
            turns.append(_turn(archetype_key, name, chunk_index, f"c{chunk_index}", _score(2, 2, 2)))
            assert session_is_complete(_session(*turns)) is False

    # DA: initial + 2 follow-ups, reusing chunk 0 (a different chunk than the turn
    # immediately preceding it, so follow_ups_on_current_topic starts fresh for DA's
    # own chain rather than inheriting E's trailing count).
    for i in range(1 + 2):
        turns.append(_turn(DEVILS_ADVOCATE_KEY, "Marlowe", 0, "c0", _score(2, 2, 2)))
        session = _session(*turns)
        assert session_is_complete(session) is (i == 2)

    assert len(turns) == 12
    assert t_max(session) == 12


def test_t_max_backstop_fires_independently_of_the_natural_conclusion_check():
    # Contrived: 12 turns from a single panelist, all strong (never triggers DA's own
    # conclusion path) — isolates the backstop from the natural-termination path.
    turns = [_turn("methodology_expert", "Reyes", i, f"c{i}", _score(5, 5, 5)) for i in range(12)]
    session = _session(*turns)
    assert session_is_complete(session) is True


def test_t_max_backstop_does_not_fire_before_reaching_it():
    turns = [_turn("methodology_expert", "Reyes", i, f"c{i}", _score(5, 5, 5)) for i in range(11)]
    session = _session(*turns)
    assert session_is_complete(session) is False


# --- v0.3i: DA retention scope fix (docs/v0.3h-i-da-retention-scope-fix.md) ---
#
# follow_ups_on_current_topic used to count trailing turns sharing chunk_index alone,
# regardless of asker. _generate_da_question deliberately reuses the contested turn's
# chunk (engine.py:288), so when DA's target happened to be the chunk a prior panelist
# had just exhausted MAX_FOLLOW_UPS_PER_TOPIC on, DA inherited that exhausted count and
# got no follow-up chain of its own — ending the session for a reason unrelated to
# either documented end condition (v0.3f Decision 5). The fix rescopes the count to
# require BOTH chunk_index and panelist_archetype_key to match the current turn.
#
# Call-site audit (v0.3i): grep for `follow_ups_on_current_topic` across the repo found
# exactly one production consumer — `_should_follow_up` at engine.py:156 — and one test
# consumer, test_branching.py's `test_followups_counted_per_chunk_not_globally`. No
# other call site needs the old chunk-only meaning: `used_chunk_indices` is a separate,
# unaffected property (chunk variety for rotation/retrieval exclusion, not the
# retention cap) and was not touched. The one existing test consumer uses a single
# panelist throughout, so its assertions hold unchanged under the new scoping — see the
# updated comment at test_branching.py's `_CASES` block.


def test_da_not_blocked_by_another_panelists_exhausted_chunk_regression_shape():
    """Exact reported repro shape: panelist M exhausts the cap on chunk 51 (initial + 2
    follow-ups), DA's very next turn contests that claim and reuses chunk 51. Under the
    old chunk-only scoping this inherited M's spent count and silently denied DA any
    follow-up chain. Rescoped by asker, DA's chain starts fresh."""
    m1 = _turn("methodology_expert", "Reyes", 51, "c51", _score(1, 1, 1))
    m2 = _turn("methodology_expert", "Reyes", 51, "c51", _score(1, 1, 1))
    m3 = _turn("methodology_expert", "Reyes", 51, "c51", _score(1, 1, 1))
    da1 = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 51, "c51", _score(1, 1, 1))
    session = _session(m1, m2, m3, da1)

    assert session.follow_ups_on_current_topic == 0  # DA's own chain, fresh
    assert _should_follow_up(session) is True  # not blocked by M's spent follow-ups
    assert session_is_complete(session) is False  # neither documented end condition met


def test_da_own_chain_reaching_cap_ends_session():
    m = _turn("methodology_expert", "Reyes", 51, "c51", _score(1, 1, 1))
    da1 = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 51, "c51", _score(1, 1, 1))
    da2 = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 51, "c51", _score(1, 1, 1))
    da3 = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 51, "c51", _score(1, 1, 1))
    session = _session(m, da1, da2, da3)

    assert session.follow_ups_on_current_topic == MAX_FOLLOW_UPS_PER_TOPIC
    assert _should_follow_up(session) is False  # DA's own chain hit the cap
    assert session_is_complete(session) is True


def test_da_answering_strongly_ends_session():
    m = _turn("methodology_expert", "Reyes", 51, "c51", _score(1, 1, 1))
    da_strong = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 51, "c51", _score(5, 5, 5))
    session = _session(m, da_strong)

    assert _should_follow_up(session) is False  # strong answer, no follow-up earned
    assert session_is_complete(session) is True


def test_non_da_retention_still_caps_per_panelist_on_one_chunk():
    """Same-panelist chain on one chunk is unaffected by the rescoping — the new
    panelist-match condition is trivially satisfied when it's the same asker throughout,
    so this collapses to the pre-v0.3i chunk-only behavior."""
    m1 = _turn("methodology_expert", "Reyes", 7, "c7", _score(2, 2, 2))
    m2 = _turn("methodology_expert", "Reyes", 7, "c7", _score(2, 2, 2))
    m3 = _turn("methodology_expert", "Reyes", 7, "c7", _score(1, 1, 1))
    session = _session(m1, m2, m3)

    assert session.follow_ups_on_current_topic == MAX_FOLLOW_UPS_PER_TOPIC
    assert _should_follow_up(session) is False  # cap reached, forced to a new topic


# --- Devil's Advocate target selection (Task 3) ---


class _StubProvider:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def generate_structured(self, prompt, response_model):
        self.calls += 1
        self.last_prompt = prompt
        return self._response


_DA_PANELIST = Panelist(archetype_key=DEVILS_ADVOCATE_KEY, panelist_name="Marlowe", persona_framing="f", icon="🎓")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """`_generate_da_question` paces real API calls with time.sleep(); these tests use a
    stub provider (no network), so the pacing delay is pure overhead — patch it out."""
    monkeypatch.setattr(engine_module.time, "sleep", lambda _seconds: None)


def test_da_selects_highest_scored_prior_claim_and_reuses_its_chunk():
    weak = _turn("methodology_expert", "Reyes", 1, "chunk one text", _score(2, 2, 2), question="q1")
    strongest = _turn("literature_theory_specialist", "Okafor", 2, "chunk two text", _score(5, 4, 4), question="q2")
    mid = _turn("ethics_practicality_reviewer", "Alvarez", 3, "chunk three text", _score(3, 3, 3), question="q3")
    session = _session(weak, strongest, mid)

    response = PanelistQuestion(question="challenge", grounding_reference="chunk two text", difficulty_level=3)
    provider = _StubProvider(response)

    result = _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")

    assert result.chunk_index == 2
    assert result.chunk_text == "chunk two text"
    assert result.panelist_archetype_key == DEVILS_ADVOCATE_KEY
    assert result.panelist_name == "Marlowe"
    assert "q2" in provider.last_prompt  # targeted the strongest claim's original question
    assert "Okafor" in provider.last_prompt


def test_da_excludes_its_own_prior_turns_from_target_selection():
    own_prior = _turn(DEVILS_ADVOCATE_KEY, "Marlowe", 9, "da's own chunk", _score(5, 5, 5), question="da q")
    only_candidate = _turn("methodology_expert", "Reyes", 1, "chunk one text", _score(1, 1, 1), question="q1")
    session = _session(own_prior, only_candidate)

    response = PanelistQuestion(question="challenge", grounding_reference="chunk one text", difficulty_level=3)
    provider = _StubProvider(response)

    result = _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")
    assert result.chunk_index == 1  # not DA's own prior chunk 9


def test_da_raises_when_no_candidate_turn_exists():
    session = _session()  # no turns at all
    provider = _StubProvider(PanelistQuestion(question="x", grounding_reference="x", difficulty_level=1))
    with pytest.raises(RuntimeError):
        _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")


def test_da_grounding_failure_warns_not_crashes(caplog):
    target = _turn("methodology_expert", "Reyes", 1, "the actual document excerpt", _score(4, 4, 4), question="q1")
    session = _session(target)

    # grounding_reference deliberately not present in the chunk_text
    response = PanelistQuestion(
        question="challenge", grounding_reference="something never in the chunk", difficulty_level=3
    )
    provider = _StubProvider(response)

    with caplog.at_level(logging.WARNING):
        result = _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")

    assert result.grounding_reference == "something never in the chunk"  # not swapped/exempted, just logged
    assert "Grounding check failed" in caplog.text


# --- v0.3 hardening: is_grounded() enforcement escalation (Decision 2/4, Tasks 2a/2c) ---


class _SequenceStubProvider:
    """Like _StubProvider, but returns a different canned response per call, in order —
    needed to simulate a first-attempt failure followed by a retry's outcome."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.prompts = []

    def generate_structured(self, prompt, response_model):
        self.prompts.append(prompt)
        response = self._responses[self.calls]
        self.calls += 1
        return response


def test_conversation_turn_grounding_fields_default_false():
    turn = _turn("methodology_expert", "Reyes", 1, "chunk text", _score(3, 3, 3))
    assert turn.grounding_retry_used is False
    assert turn.grounding_flagged is False


def test_da_below_difficulty_4_grounding_failure_does_not_retry():
    target = _turn("methodology_expert", "Reyes", 1, "the actual document excerpt", _score(4, 4, 4))
    session = _session(target)
    session.difficulty_current = 3  # below the enforcement floor

    response = PanelistQuestion(question="challenge", grounding_reference="never in the chunk", difficulty_level=3)
    provider = _SequenceStubProvider([response])

    result = _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")

    assert provider.calls == 1  # no retry below difficulty 4 -- unchanged v0.2.5 behavior
    assert result.grounding_retry_used is False
    assert result.grounding_flagged is False


def test_da_at_difficulty_4_retries_once_and_clears_flag_on_success():
    target = _turn("methodology_expert", "Reyes", 1, "the actual document excerpt", _score(4, 4, 4))
    session = _session(target)
    session.difficulty_current = 4  # at the enforcement floor

    first_attempt = PanelistQuestion(question="q1", grounding_reference="never in the chunk", difficulty_level=4)
    retry_attempt = PanelistQuestion(question="q2", grounding_reference="the actual document excerpt", difficulty_level=4)
    provider = _SequenceStubProvider([first_attempt, retry_attempt])

    result = _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")

    assert provider.calls == 2  # exactly one retry, same prompt (no re-retrieval)
    assert provider.prompts[0] == provider.prompts[1]
    assert result.question == "q2"  # the retry's output is what gets served
    assert result.grounding_retry_used is True
    assert result.grounding_flagged is False  # retry succeeded


def test_da_at_difficulty_5_double_failure_serves_anyway_and_flags(caplog):
    target = _turn("methodology_expert", "Reyes", 1, "the actual document excerpt", _score(4, 4, 4))
    session = _session(target)
    session.difficulty_current = 5

    first_attempt = PanelistQuestion(question="q1", grounding_reference="never in the chunk", difficulty_level=5)
    retry_attempt = PanelistQuestion(question="q2", grounding_reference="still not in the chunk", difficulty_level=5)
    provider = _SequenceStubProvider([first_attempt, retry_attempt])

    with caplog.at_level(logging.WARNING):
        result = _generate_da_question(provider, session, _DA_PANELIST, "gemini-3.1-flash-lite")

    assert provider.calls == 2
    assert result.question == "q2"  # served anyway, per Decision 4 -- never dropped
    assert result.grounding_retry_used is True
    assert result.grounding_flagged is True
    assert "retry also failed" in caplog.text.lower()


_METHODOLOGY_PANELIST = Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓")


def _patch_retrieve(monkeypatch, chunk_index, chunk_text):
    chunk = Chunk(text=chunk_text, embedding=[0.0])
    monkeypatch.setattr(engine_module, "retrieve", lambda *a, **kw: [(chunk_index, chunk)])
    return chunk


def test_new_topic_high_difficulty_guard_present_and_retry_fires_at_difficulty_4(monkeypatch):
    _patch_retrieve(monkeypatch, 0, "the actual document excerpt with real numbers")
    session = _session()
    session.difficulty_current = 4

    first_attempt = PanelistQuestion(question="q1", grounding_reference="never in the chunk", difficulty_level=4)
    retry_attempt = PanelistQuestion(
        question="q2", grounding_reference="the actual document excerpt", difficulty_level=4
    )
    provider = _SequenceStubProvider([first_attempt, retry_attempt])

    turn = _generate_question(
        provider, session, chunks=[], embedding_model=None, panelist=_METHODOLOGY_PANELIST,
        other_subtype_line="", model="gemini-3.1-flash-lite",
    )

    assert provider.calls == 2
    assert turn.grounding_retry_used is True
    assert turn.grounding_flagged is False
    # Decision 3's guard text is injected at difficulty >= 4, identically into both attempts.
    assert HIGH_DIFFICULTY_GROUNDING_GUARD.strip() in provider.prompts[0]
    assert HIGH_DIFFICULTY_GROUNDING_GUARD.strip() in provider.prompts[1]


def test_new_topic_high_difficulty_guard_absent_below_difficulty_4(monkeypatch):
    _patch_retrieve(monkeypatch, 0, "the actual document excerpt")
    session = _session()
    session.difficulty_current = 3  # below the enforcement floor

    response = PanelistQuestion(question="q1", grounding_reference="never in the chunk", difficulty_level=3)
    provider = _SequenceStubProvider([response])

    turn = _generate_question(
        provider, session, chunks=[], embedding_model=None, panelist=_METHODOLOGY_PANELIST,
        other_subtype_line="", model="gemini-3.1-flash-lite",
    )

    assert provider.calls == 1  # no retry below the floor -- unchanged v0.2.5 behavior
    assert turn.grounding_retry_used is False
    assert turn.grounding_flagged is False
    assert HIGH_DIFFICULTY_GROUNDING_GUARD.strip() not in provider.prompts[0]
