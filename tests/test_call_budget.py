"""call_budget: worst-case calls per session, and the loop bounds it reads, pinned to
what the code actually does. No network: providers are stubs."""

from __future__ import annotations

import pytest

from academic_defense_simulator.call_budget import CallRole, calls_per_model, worst_case_calls
from academic_defense_simulator.engine import (
    GROUNDING_ATTEMPTS_MAX,
    GROUNDING_ENFORCEMENT_DIFFICULTY_FLOOR,
    _generate_with_grounding_enforcement,
    t_max,
    turn_cap,
)
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import GeneratedPanelist, PanelGeneration
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import (
    DEVILS_ADVOCATE_KEY,
    FALLBACK_PANELISTS,
    PANEL_COMPOSITION,
    PERSONA_GENERATION_ATTEMPTS,
    apply_customizations,
    generate_panel,
)

_KEYS = PANEL_COMPOSITION["thesis"][:3]


def _profile() -> DefenseProfile:
    return DefenseProfile(
        defense_type=DefenseType.THESIS, domain="d", topic="t", selected_archetypes=_KEYS, document_id="doc"
    )


class _CountingProvider:
    """Returns the same response to every call and counts them."""

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def generate_structured(self, prompt, response_model):
        self.calls += 1
        return self.response


def test_turn_cap_matches_t_max():
    panel = apply_customizations([FALLBACK_PANELISTS[key] for key in [*_KEYS, DEVILS_ADVOCATE_KEY]], _profile())
    session = DefenseSession(profile=_profile(), panel=panel, difficulty_current=2)
    assert t_max(session) == turn_cap(4) == 12


def test_grounding_attempts_max_is_what_the_code_makes():
    never_grounded = PanelistQuestion(
        question="q", grounding_reference="text that is not in the chunk", difficulty_level=4
    )
    provider = _CountingProvider(never_grounded)
    _generate_with_grounding_enforcement(
        provider, "prompt", "the chunk", GROUNDING_ENFORCEMENT_DIFFICULTY_FLOOR, "m"
    )
    assert provider.calls == GROUNDING_ATTEMPTS_MAX


def test_persona_generation_attempts_is_what_the_code_makes():
    wrong_roster = PanelGeneration(
        panelists=[GeneratedPanelist(archetype_key="not_a_key", panelist_name="X", persona_framing="f")]
    )
    provider = _CountingProvider(wrong_roster)  # a malformed roster every time: both attempts used
    _, fallback_used = generate_panel(_profile(), _KEYS, provider)
    assert fallback_used and provider.calls == PERSONA_GENERATION_ATTEMPTS


def test_worst_case_for_a_full_panel():
    calls = worst_case_calls(4, extraction=True, relevance_check=False)
    assert calls == {
        CallRole.EXTRACTION: 1,
        CallRole.PERSONA: 2,
        CallRole.QUESTION: 24,
        CallRole.SCORING: 12,
        CallRole.NARRATIVE: 1,
        CallRole.SUGGESTIONS: 1,
        CallRole.RELEVANCE_CHECK: 0,
    }


def test_calls_per_model_with_the_m2_split():
    defense, suggestions = "gemini-3.1-flash-lite", "gemini-2.5-flash"
    roles = {role: defense for role in CallRole} | {CallRole.SUGGESTIONS: suggestions}
    totals = calls_per_model(worst_case_calls(4, extraction=True, relevance_check=False), roles)
    assert totals == {defense: 40, suggestions: 1}


def test_calls_per_model_with_one_model_for_everything():
    totals = calls_per_model(
        worst_case_calls(4, extraction=False, relevance_check=True), {role: "m" for role in CallRole}
    )
    assert totals == {"m": 2 + 24 + 12 + 1 + 1 + 1}


def test_a_role_that_makes_calls_needs_a_model():
    calls = worst_case_calls(4, extraction=False, relevance_check=False)
    roles = {role: "m" for role in CallRole if role not in (CallRole.SUGGESTIONS, CallRole.EXTRACTION)}
    with pytest.raises(ValueError, match="suggestions"):  # extraction makes 0 calls, so it may be missing
        calls_per_model(calls, roles)
