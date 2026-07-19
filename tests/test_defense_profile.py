"""DefenseProfile schema tests (v0.3e Decision 1) — direct archetype selection
replaces panel_size. Pure Pydantic validation, zero LLM calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from academic_defense_simulator.models.defense_profile import (
    DefenseProfile,
    DefenseType,
    OtherSubtype,
    PanelistCustomization,
)


def _profile(**overrides):
    fields = dict(
        defense_type=DefenseType.THESIS,
        other_subtype=None,
        domain="library science",
        topic="t",
        selected_archetypes=["methodology_expert"],
        document_id="doc",
    )
    fields.update(overrides)
    return DefenseProfile(**fields)


def test_valid_subset_passes():
    profile = _profile(selected_archetypes=["methodology_expert", "literature_theory_specialist"])
    assert profile.selected_archetypes == ["methodology_expert", "literature_theory_specialist"]


def test_invalid_archetype_key_rejected_for_type():
    # technical_implementation_reviewer is a real archetype key, just not in oral_comps'
    # roster (PANEL_COMPOSITION["other/oral_comps"]) — a genuine "not valid for this type"
    # case, not just a typo.
    with pytest.raises(ValidationError, match="not valid for 'other/oral_comps'"):
        _profile(
            defense_type=DefenseType.OTHER,
            other_subtype=OtherSubtype.ORAL_COMPS,
            selected_archetypes=["technical_implementation_reviewer"],
        )


def test_devils_advocate_is_never_a_valid_selection():
    with pytest.raises(ValidationError, match="not valid for 'thesis'"):
        _profile(selected_archetypes=["devils_advocate"])


def test_duplicate_rejected():
    with pytest.raises(ValidationError, match="duplicates"):
        _profile(selected_archetypes=["methodology_expert", "methodology_expert"])


def test_more_than_three_rejected():
    with pytest.raises(ValidationError):
        _profile(
            selected_archetypes=[
                "methodology_expert",
                "literature_theory_specialist",
                "ethics_practicality_reviewer",
                "technical_implementation_reviewer",
            ]
        )


def test_empty_selection_rejected():
    with pytest.raises(ValidationError):
        _profile(selected_archetypes=[])


# --- panel_customizations validator (v0.3j Decision 2) ---


def test_customization_key_not_on_panel_rejected():
    # literature_theory_specialist is a real archetype key, just not among this
    # profile's selected_archetypes — customizing an unseated slot is an error.
    with pytest.raises(ValidationError, match="not on this panel"):
        _profile(
            selected_archetypes=["methodology_expert"],
            panel_customizations=[
                PanelistCustomization(archetype_key="literature_theory_specialist", display_name="Okafor")
            ],
        )


def test_customization_da_key_accepted():
    # DA is never in selected_archetypes (v0.3e Decision 5) but is always seated,
    # so it is always customizable.
    profile = _profile(
        selected_archetypes=["methodology_expert"],
        panel_customizations=[
            PanelistCustomization(archetype_key="devils_advocate", display_name="Vance", icon="⚔️")
        ],
    )
    assert profile.panel_customizations[0].display_name == "Vance"


def test_customization_duplicate_keys_rejected():
    with pytest.raises(ValidationError, match="duplicate archetype keys"):
        _profile(
            selected_archetypes=["methodology_expert"],
            panel_customizations=[
                PanelistCustomization(archetype_key="methodology_expert", display_name="Reyes"),
                PanelistCustomization(archetype_key="methodology_expert", icon="🧠"),
            ],
        )


def test_customization_empty_list_fine():
    profile = _profile(panel_customizations=[])
    assert profile.panel_customizations == []
