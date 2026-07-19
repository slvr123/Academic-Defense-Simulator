"""Panel composition + persona generation tests (0.3a Tasks 1 and 3).

Composition tests are pure, zero-LLM. Persona generation tests use a mocked provider —
no network, no real LLM calls; the live-call evidence for generate_panel lives in the
session report, not here.
"""

from __future__ import annotations

import logging

import pytest

from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.defense_profile import (
    DefenseProfile,
    DefenseType,
    OtherSubtype,
    PanelistCustomization,
)
from academic_defense_simulator.models.panelist import GeneratedPanelist, PanelGeneration
from academic_defense_simulator.panel import (
    ARCHETYPE_DEFAULT_ICONS,
    DEVILS_ADVOCATE_KEY,
    FALLBACK_PANELISTS,
    PANEL_COMPOSITION,
    apply_customizations,
    compose_panel,
    generate_panel,
)


def _profile(defense_type, other_subtype=None, selected_archetypes=None, panel_customizations=None):
    key = defense_type.value if other_subtype is None else f"other/{other_subtype.value}"
    if selected_archetypes is None:
        selected_archetypes = PANEL_COMPOSITION[key][:3]
    return DefenseProfile(
        defense_type=defense_type,
        other_subtype=other_subtype,
        domain="library science",
        topic="t",
        selected_archetypes=selected_archetypes,
        document_id="doc",
        panel_customizations=panel_customizations or [],
    )


@pytest.mark.parametrize(
    "defense_type,other_subtype",
    [
        (DefenseType.THESIS, None),
        (DefenseType.CAPSTONE, None),
        (DefenseType.OTHER, OtherSubtype.ORAL_COMPS),
        (DefenseType.OTHER, OtherSubtype.SCHOLARSHIP_PANEL),
        (DefenseType.OTHER, OtherSubtype.GRANT_DEFENSE),
        (DefenseType.OTHER, OtherSubtype.CERTIFICATION_INTERVIEW),
    ],
)
def test_selection_up_to_the_cap_matches_natural_order_slice(defense_type, other_subtype):
    """v0.3e Decision 3: cap is 3 for every defense type, uniformly — this is the
    direct-selection replacement for the old 'full roster' truncation test."""
    key = defense_type.value if other_subtype is None else f"other/{other_subtype.value}"
    roster = PANEL_COMPOSITION[key]
    selection = roster[:3]  # the max allowed, regardless of how many the type's roster has
    profile = _profile(defense_type, other_subtype, selected_archetypes=selection)
    assert compose_panel(profile) == selection


@pytest.mark.parametrize("size", [1, 2, 3])
def test_selection_size_is_preserved(size):
    selection = PANEL_COMPOSITION["thesis"][:size]
    profile = _profile(DefenseType.THESIS, selected_archetypes=selection)
    result = compose_panel(profile)
    assert result == selection
    assert len(result) == size


def test_compose_panel_returns_natural_order_not_click_order():
    """v0.3e Decision 4: compose_panel derives speaking order from PANEL_COMPOSITION's
    priority order, not from the order archetypes appear in selected_archetypes."""
    roster = PANEL_COMPOSITION["thesis"]
    assert roster == [
        "methodology_expert",
        "literature_theory_specialist",
        "ethics_practicality_reviewer",
        "technical_implementation_reviewer",
    ]
    # Click order deliberately reversed relative to the roster's priority order.
    click_order = ["technical_implementation_reviewer", "methodology_expert"]
    profile = _profile(DefenseType.THESIS, selected_archetypes=click_order)
    assert compose_panel(profile) == ["methodology_expert", "technical_implementation_reviewer"]


# --- generate_panel (Task 3) ---

_ROSTER = ["methodology_expert", "literature_theory_specialist"]


class _StubProvider:
    """Mocked LLMProvider: returns queued responses/exceptions in order, one per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def generate_structured(self, prompt, response_model):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _generation_profile():
    return _profile(DefenseType.THESIS, selected_archetypes=_ROSTER)


def _gen(panelists):
    return PanelGeneration(panelists=panelists)


def _panelist(key, name, framing="framing"):
    return GeneratedPanelist(archetype_key=key, panelist_name=name, persona_framing=framing)


def _assert_is_seated_fallback(panel):
    """The seated (post-customization) roster produced from FALLBACK_PANELISTS with an
    empty customization list: fallback names, archetype-default icons."""
    assert [(p.archetype_key, p.panelist_name, p.icon) for p in panel] == [
        (k, FALLBACK_PANELISTS[k].panelist_name, ARCHETYPE_DEFAULT_ICONS[k]) for k in _ROSTER
    ]


def test_unknown_key_falls_back_after_retry(caplog):
    malformed = _gen([_panelist("methodology_expert", "Cruz"), _panelist("bogus_key", "Diaz")])
    provider = _StubProvider([malformed, malformed])
    with caplog.at_level(logging.WARNING):
        panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is True
    _assert_is_seated_fallback(panel)
    assert provider.calls == 2
    assert "malformed roster" in caplog.text or "falling back" in caplog.text


def test_missing_key_falls_back_after_retry():
    malformed = _gen([_panelist("methodology_expert", "Cruz")])  # only 1 of 2 requested
    provider = _StubProvider([malformed, malformed])
    panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is True
    _assert_is_seated_fallback(panel)


def test_duplicate_surname_falls_back_after_retry():
    malformed = _gen(
        [
            _panelist("methodology_expert", "Cruz"),
            _panelist("literature_theory_specialist", "cruz"),  # same surname, case-insensitive
        ]
    )
    provider = _StubProvider([malformed, malformed])
    panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is True
    _assert_is_seated_fallback(panel)


def test_order_mismatch_is_reordered_not_a_failure():
    reversed_order = _gen(
        [
            _panelist("literature_theory_specialist", "Okafor", "lit framing"),
            _panelist("methodology_expert", "Reyes", "method framing"),
        ]
    )
    provider = _StubProvider([reversed_order])
    panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is False
    assert [p.archetype_key for p in panel] == _ROSTER
    assert provider.calls == 1


def test_valid_response_passes_through():
    valid = _gen(
        [
            _panelist("methodology_expert", "Reyes", "method framing"),
            _panelist("literature_theory_specialist", "Okafor", "lit framing"),
        ]
    )
    provider = _StubProvider([valid])
    panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is False
    assert [(p.archetype_key, p.panelist_name, p.persona_framing) for p in panel] == [
        ("methodology_expert", "Reyes", "method framing"),
        ("literature_theory_specialist", "Okafor", "lit framing"),
    ]
    assert [p.icon for p in panel] == [ARCHETYPE_DEFAULT_ICONS[k] for k in _ROSTER]
    assert provider.calls == 1


def test_provider_error_then_success_recovers_without_fallback():
    valid = _gen(
        [
            _panelist("methodology_expert", "Reyes", "method framing"),
            _panelist("literature_theory_specialist", "Okafor", "lit framing"),
        ]
    )
    provider = _StubProvider([LLMProviderError("transient"), valid])
    panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is False
    assert [(p.archetype_key, p.panelist_name) for p in panel] == [
        ("methodology_expert", "Reyes"),
        ("literature_theory_specialist", "Okafor"),
    ]


# --- apply_customizations (v0.3j Task 5) ---


def test_blank_or_missing_display_name_keeps_generated_name():
    """None, absent, and whitespace-only display_name all mean 'keep the generated
    name' (Decision 2: None/blank → keep)."""
    generated = [_panelist("methodology_expert", "Cruz"), _panelist("literature_theory_specialist", "Diaz")]
    profile = _profile(
        DefenseType.THESIS,
        selected_archetypes=_ROSTER,
        panel_customizations=[
            PanelistCustomization(archetype_key="methodology_expert", display_name="   "),
            # literature_theory_specialist has no customization entry at all
        ],
    )
    panel = apply_customizations(generated, profile)
    assert [p.panelist_name for p in panel] == ["Cruz", "Diaz"]


def test_display_name_override_replaces_generated_name():
    generated = [_panelist("methodology_expert", "Cruz"), _panelist("literature_theory_specialist", "Diaz")]
    profile = _profile(
        DefenseType.THESIS,
        selected_archetypes=_ROSTER,
        panel_customizations=[
            PanelistCustomization(archetype_key="methodology_expert", display_name="  Reyes  "),
        ],
    )
    panel = apply_customizations(generated, profile)
    assert [p.panelist_name for p in panel] == ["Reyes", "Diaz"]
    assert panel[0].persona_framing == generated[0].persona_framing  # framing untouched


def test_icon_resolution_default_and_override():
    """Icon is resolved for every panelist unconditionally: customization icon where
    set, archetype default everywhere else — no Panelist ever lacks an icon."""
    generated = [_panelist("methodology_expert", "Cruz"), _panelist("literature_theory_specialist", "Diaz")]
    profile = _profile(
        DefenseType.THESIS,
        selected_archetypes=_ROSTER,
        panel_customizations=[
            PanelistCustomization(archetype_key="methodology_expert", icon="🧠"),
        ],
    )
    panel = apply_customizations(generated, profile)
    assert panel[0].icon == "🧠"
    assert panel[1].icon == ARCHETYPE_DEFAULT_ICONS["literature_theory_specialist"]


def test_devils_advocate_customization_applies():
    generated = [_panelist(DEVILS_ADVOCATE_KEY, "Marlowe")]
    profile = _profile(
        DefenseType.THESIS,
        selected_archetypes=_ROSTER,
        panel_customizations=[
            PanelistCustomization(archetype_key=DEVILS_ADVOCATE_KEY, display_name="Vance", icon="🏛️"),
        ],
    )
    panel = apply_customizations(generated, profile)
    assert (panel[0].panelist_name, panel[0].icon) == ("Vance", "🏛️")


def test_custom_name_survives_fallback_path():
    """A user's custom name/icon must survive persona-generation failure — both
    generate_panel exits pass through apply_customizations."""
    provider = _StubProvider([LLMProviderError("down"), LLMProviderError("still down")])
    profile = _profile(
        DefenseType.THESIS,
        selected_archetypes=_ROSTER,
        panel_customizations=[
            PanelistCustomization(archetype_key="methodology_expert", display_name="Reyes-Santos", icon="📊"),
        ],
    )
    panel, fallback_used = generate_panel(profile, _ROSTER, provider)
    assert fallback_used is True
    assert (panel[0].panelist_name, panel[0].icon) == ("Reyes-Santos", "📊")
    assert panel[1].panelist_name == FALLBACK_PANELISTS["literature_theory_specialist"].panelist_name
