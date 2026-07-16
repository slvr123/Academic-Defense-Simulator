"""DefenseProfile schema tests (v0.3e Decision 1) — direct archetype selection
replaces panel_size. Pure Pydantic validation, zero LLM calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype


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
