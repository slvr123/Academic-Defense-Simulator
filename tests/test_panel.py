"""Panel composition + persona generation tests (0.3a Tasks 1 and 3).

Composition tests are pure, zero-LLM. Persona generation tests use a mocked provider —
no network, no real LLM calls; the live-call evidence for generate_panel lives in the
session report, not here.
"""

from __future__ import annotations

import logging

import pytest

from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.panelist import Panelist, PanelGeneration
from academic_defense_simulator.panel import FALLBACK_PANELISTS, PANEL_COMPOSITION, compose_panel, generate_panel


def _profile(defense_type, other_subtype=None, panel_size=1):
    return DefenseProfile(
        defense_type=defense_type,
        other_subtype=other_subtype,
        domain="library science",
        topic="t",
        panel_size=panel_size,
        document_id="doc",
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
def test_full_roster_matches_composition_table(defense_type, other_subtype):
    key = defense_type.value if other_subtype is None else f"other/{other_subtype.value}"
    full_roster = PANEL_COMPOSITION[key]
    profile = _profile(defense_type, other_subtype, panel_size=len(full_roster))
    assert compose_panel(profile) == full_roster


@pytest.mark.parametrize("size", [1, 2, 3])
def test_truncation_at_panel_size(size):
    profile = _profile(DefenseType.THESIS, panel_size=size)
    result = compose_panel(profile)
    assert result == PANEL_COMPOSITION["thesis"][:size]
    assert len(result) == size


def test_underfilled_panel_raises_value_error_naming_subtype_and_max():
    profile = _profile(DefenseType.OTHER, OtherSubtype.ORAL_COMPS, panel_size=4)
    with pytest.raises(ValueError) as exc_info:
        compose_panel(profile)
    message = str(exc_info.value)
    assert "other/oral_comps" in message
    assert "3" in message  # max panel size for this subtype


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
    return _profile(DefenseType.THESIS, panel_size=2)


def _gen(panelists):
    return PanelGeneration(panelists=panelists)


def _panelist(key, name, framing="framing"):
    return Panelist(archetype_key=key, panelist_name=name, persona_framing=framing)


def test_unknown_key_falls_back_after_retry(caplog):
    malformed = _gen([_panelist("methodology_expert", "Cruz"), _panelist("bogus_key", "Diaz")])
    provider = _StubProvider([malformed, malformed])
    with caplog.at_level(logging.WARNING):
        panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is True
    assert panel == [FALLBACK_PANELISTS[k] for k in _ROSTER]
    assert provider.calls == 2
    assert "malformed roster" in caplog.text or "falling back" in caplog.text


def test_missing_key_falls_back_after_retry():
    malformed = _gen([_panelist("methodology_expert", "Cruz")])  # only 1 of 2 requested
    provider = _StubProvider([malformed, malformed])
    panel, fallback_used = generate_panel(_generation_profile(), _ROSTER, provider)
    assert fallback_used is True
    assert panel == [FALLBACK_PANELISTS[k] for k in _ROSTER]


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
    assert panel == [FALLBACK_PANELISTS[k] for k in _ROSTER]


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
    assert panel == valid.panelists
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
    assert panel == valid.panelists
