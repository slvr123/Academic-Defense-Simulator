"""M12 Decision 1: the opening round is seated by home chapter, Devil's Advocate last,
and v1's engine then opens topics in that order with no v1 change."""

from __future__ import annotations

import pytest

from academic_defense_simulator.engine import record_scored_turn, select_active_panelist
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY, PANEL_COMPOSITION
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from backend.opening_order import HOME_CHAPTER, opening_order
from backend.tests.conftest import make_pdf, upload, wait_for


def seat(*keys: str) -> list[Panelist]:
    return [Panelist(archetype_key=k, panelist_name=k[:6], persona_framing="", icon="x") for k in keys]


def keys(panel: list[Panelist]) -> list[str]:
    return [p.archetype_key for p in panel]


def test_every_domain_archetype_has_a_home_chapter():
    domain = set(ARCHETYPE_CONFIG) - {DEVILS_ADVOCATE_KEY}
    assert set(HOME_CHAPTER) == domain
    assert {k for roster in PANEL_COMPOSITION.values() for k in roster} <= set(HOME_CHAPTER)


def test_the_thesis_recommended_panel_opens_at_chapter_one():
    panel = seat("methodology_expert", "problem_objectives_reviewer", "results_conclusions_reviewer", DEVILS_ADVOCATE_KEY)
    assert keys(opening_order(panel)) == [
        "problem_objectives_reviewer", "methodology_expert", "results_conclusions_reviewer", DEVILS_ADVOCATE_KEY,
    ]


def test_ties_keep_their_relative_order():
    # All three are home chapter 3; both input orders survive unchanged.
    tied = ["technical_implementation_reviewer", "methodology_expert", "statistical_analysis_reviewer"]
    assert keys(opening_order(seat(*tied, DEVILS_ADVOCATE_KEY))) == [*tied, DEVILS_ADVOCATE_KEY]
    assert keys(opening_order(seat(*reversed(tied)))) == list(reversed(tied))
    # A tie among two chapter-5 keys behind a chapter-1 key.
    panel = seat("industry_practice_reviewer", "ethics_practicality_reviewer", "problem_objectives_reviewer")
    assert keys(opening_order(panel)) == [
        "problem_objectives_reviewer", "industry_practice_reviewer", "ethics_practicality_reviewer",
    ]


def test_devils_advocate_is_last_wherever_it_was_seated():
    panel = seat(DEVILS_ADVOCATE_KEY, "results_conclusions_reviewer", "literature_theory_specialist")
    assert keys(opening_order(panel)) == [
        "literature_theory_specialist", "results_conclusions_reviewer", DEVILS_ADVOCATE_KEY,
    ]


def test_an_archetype_without_a_home_chapter_is_refused():
    with pytest.raises(KeyError):
        opening_order(seat("not_an_archetype"))


def _strong_turn(key: str, chunk: int) -> ConversationTurn:
    return ConversationTurn(
        panelist_archetype_key=key, panelist_name=key[:6], question="?", answer="a",
        difficulty_level=2, grounding_reference="", chunk_index=chunk, chunk_text="",
        score=AnswerScore(clarity=5, depth=5, grounding=5, difficulty_delta=0, primary_gap="none", answer_summary="s"),
    )


def test_v1_opens_topics_in_the_seated_order_then_devils_advocate():
    """The seam: v1's select_active_panelist, unchanged, follows the order opening_order seats."""
    panel = opening_order(seat(
        "results_conclusions_reviewer", "methodology_expert", "problem_objectives_reviewer", DEVILS_ADVOCATE_KEY,
    ))
    profile = DefenseProfile(
        defense_type=DefenseType.THESIS, domain="d", topic="t", document_id="doc",
        selected_archetypes=["results_conclusions_reviewer", "methodology_expert", "problem_objectives_reviewer"],
    )
    session = DefenseSession(profile=profile, panel=panel, difficulty_current=2)
    spoken = []
    for chunk in range(4):
        key = select_active_panelist(session).archetype_key
        spoken.append(key)
        record_scored_turn(session, _strong_turn(key, chunk))
    assert spoken == [
        "problem_objectives_reviewer", "methodology_expert", "results_conclusions_reviewer", DEVILS_ADVOCATE_KEY,
    ]


def test_start_seats_the_panel_and_asks_the_chapter_one_reviewer_first(client_factory, gemini, quota_store, tmp_path):
    pdf = make_pdf(tmp_path / "Thesis.pdf", pages=16)
    chosen = '{"defense_type": "thesis", "archetype_keys": ' \
        '["methodology_expert", "problem_objectives_reviewer", "results_conclusions_reviewer"]}'
    with client_factory() as client:
        session_id = upload(client, pdf, panel=chosen).json()["session_id"]
        wait_for(client, session_id, lambda s: s.get("status") == "ready")
        body = client.post(f"/sessions/{session_id}/start").json()
    assert [p["archetype_key"] for p in body["panel"]] == [
        "problem_objectives_reviewer", "methodology_expert", "results_conclusions_reviewer", DEVILS_ADVOCATE_KEY,
    ]
    assert body["question"]["panelist"]["archetype_key"] == "problem_objectives_reviewer"


def test_panel_options_carry_each_home_chapter(client_factory):
    with client_factory() as client:
        body = client.get("/panel-options").json()
    for roster in body["rosters"]:
        for option in roster["archetypes"]:
            assert option["home_chapter"] == HOME_CHAPTER[option["archetype_key"]]
    assert body["devils_advocate"]["home_chapter"] is None
