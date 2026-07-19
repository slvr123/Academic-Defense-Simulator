"""Follow-up attribution fix tests (v0.3d — docs/v0.3d-followup-attribution-fix.md).

`_render_prior_exchange_framing` is a pure string-in/string-out function: zero LLM calls,
zero fixtures beyond a ConversationTurn/Panelist pair. Covers the same-asker and colleague
framing variants (Decision 1), and that the full FOLLOWUP_SYSTEM_PROMPT renders with no
unfilled placeholders in either case.
"""

from __future__ import annotations

from academic_defense_simulator.engine import _render_prior_exchange_framing
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.session import ConversationTurn
from academic_defense_simulator.prompts.panelist_prompts import (
    FOLLOWUP_SYSTEM_PROMPT,
    HIGH_DIFFICULTY_GROUNDING_GUARD,
)

_SCORE = AnswerScore(
    clarity=2, depth=2, grounding=2, difficulty_delta=1,
    primary_gap="sampling justification is thin", answer_summary="the candidate claimed something",
)


def _turn(archetype_key, name):
    return ConversationTurn(
        panelist_archetype_key=archetype_key,
        panelist_name=name,
        question="Why was a convenience sample used?",
        grounding_reference="g",
        chunk_index=0,
        chunk_text="c",
        difficulty_level=2,
        answer="We felt it was a reasonable choice given our timeline.",
        score=_SCORE,
    )


def test_same_asker_framing_is_the_original_wording():
    previous_turn = _turn("methodology_expert", "Reyes")
    active = Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓")

    framing = _render_prior_exchange_framing(previous_turn, active)

    assert "You previously asked" in framing
    assert "fellow panelist" not in framing


def test_colleague_framing_names_the_correct_prior_panelist_and_avoids_false_first_person():
    previous_turn = _turn("methodology_expert", "Reyes")
    active = Panelist(archetype_key="literature_theory_specialist", panelist_name="Okafor", persona_framing="f", icon="🎓")

    framing = _render_prior_exchange_framing(previous_turn, active)

    assert "Dr. Reyes" in framing
    assert "fellow panelist" in framing
    assert "You previously asked" not in framing


def test_rendered_full_prompt_has_no_unfilled_placeholders_same_asker():
    previous_turn = _turn("methodology_expert", "Reyes")
    active = Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓")

    prompt = FOLLOWUP_SYSTEM_PROMPT.format(
        panelist_name=active.panelist_name,
        persona_framing=active.persona_framing,
        archetype_title="Methodology Expert",
        archetype_focus="research design validity",
        archetype_lane="stay in methodology",
        defense_type="thesis",
        prior_exchange_framing=_render_prior_exchange_framing(previous_turn, active),
        primary_gap=previous_turn.score.primary_gap,
        retrieved_chunk="the excerpt text",
        difficulty_level=3,
        digest_block="",
        high_difficulty_guard="",
    )

    for placeholder in (
        "{previous_question}", "{previous_answer}", "{prior_exchange_framing}",
        "{primary_gap}", "{panelist_name}", "{digest_block}", "{high_difficulty_guard}",
    ):
        assert placeholder not in prompt


def test_rendered_full_prompt_has_no_unfilled_placeholders_colleague():
    previous_turn = _turn("methodology_expert", "Reyes")
    active = Panelist(archetype_key="literature_theory_specialist", panelist_name="Okafor", persona_framing="f", icon="🎓")

    prompt = FOLLOWUP_SYSTEM_PROMPT.format(
        panelist_name=active.panelist_name,
        persona_framing=active.persona_framing,
        archetype_title="Literature & Theory Specialist",
        archetype_focus="citation grounding",
        archetype_lane="stay in literature",
        defense_type="thesis",
        prior_exchange_framing=_render_prior_exchange_framing(previous_turn, active),
        primary_gap=previous_turn.score.primary_gap,
        retrieved_chunk="the excerpt text",
        difficulty_level=3,
        digest_block="",
        high_difficulty_guard=HIGH_DIFFICULTY_GROUNDING_GUARD,
    )

    for placeholder in (
        "{previous_question}", "{previous_answer}", "{prior_exchange_framing}",
        "{primary_gap}", "{panelist_name}", "{digest_block}", "{high_difficulty_guard}",
    ):
        assert placeholder not in prompt
    assert "Dr. Reyes" in prompt
