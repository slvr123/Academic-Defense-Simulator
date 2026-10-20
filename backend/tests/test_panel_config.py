"""PanelConfig: defaults, the per-defense-type panel rules, and the v1 profile it builds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from academic_defense_simulator.models.defense_profile import DefenseType, OtherSubtype
from academic_defense_simulator.panel import PANEL_COMPOSITION
from backend.schemas import MAX_DOMAIN_PANELISTS, PanelConfig


def test_defaults_are_a_thesis_with_the_recommended_panel():
    config = PanelConfig()
    assert (config.defense_type, config.domain, config.topic, config.starting_difficulty) == (
        DefenseType.THESIS, None, None, 2
    )
    assert config.domain_archetypes() == PANEL_COMPOSITION["thesis"][:3]
    assert MAX_DOMAIN_PANELISTS == 3


def test_the_recommended_panel_follows_the_defense_type():
    config = PanelConfig(defense_type="other", other_subtype="certification_interview")
    assert config.composition_key == "other/certification_interview"
    assert config.domain_archetypes() == PANEL_COMPOSITION["other/certification_interview"][:3]


def test_chosen_panelists_are_kept_in_order():
    keys = ["literature_theory_specialist", "methodology_expert"]
    assert PanelConfig(archetype_keys=keys).domain_archetypes() == keys


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"defense_type": "other"}, "other_subtype is required"),
        ({"defense_type": "capstone", "other_subtype": "grant_defense"}, "other_subtype is required"),
        ({"archetype_keys": ["devils_advocate"]}, "devils_advocate is always on the panel"),
        ({"archetype_keys": ["methodology_expert", "methodology_expert"]}, "must not repeat"),
        ({"archetype_keys": ["not_a_panelist"]}, "can't sit on a thesis panel"),
        ({"archetype_keys": PANEL_COMPOSITION["thesis"][:4]}, "at most 3 items"),
        ({"domain": "x" * 201}, "at most 200 characters"),
    ],
)
def test_invalid_panels_are_rejected(fields, message):
    with pytest.raises(ValidationError, match=message):
        PanelConfig(**fields)


def test_an_archetype_from_another_defense_type_is_rejected():
    capstone_only = next(k for k in PANEL_COMPOSITION["capstone"] if k not in PANEL_COMPOSITION["thesis"])
    assert PanelConfig(defense_type="capstone", archetype_keys=[capstone_only])
    with pytest.raises(ValidationError, match="can't sit on a thesis panel"):
        PanelConfig(archetype_keys=[capstone_only])


def test_blank_domain_and_topic_mean_read_them_from_the_document():
    config = PanelConfig(domain="  ", topic="")
    assert config.domain is None and config.topic is None


def test_to_profile_builds_v1s_defense_profile():
    config = PanelConfig(
        defense_type="other", other_subtype="grant_defense", starting_difficulty=4,
        archetype_keys=[PANEL_COMPOSITION["other/grant_defense"][1]],
    )
    profile = config.to_profile(domain="Public health", topic="Clinic outreach", document_id="s1")
    assert profile.other_subtype is OtherSubtype.GRANT_DEFENSE
    assert profile.selected_archetypes == [PANEL_COMPOSITION["other/grant_defense"][1]]
    assert (profile.difficulty_start, profile.domain, profile.topic, profile.document_id) == (
        4, "Public health", "Clinic outreach", "s1"
    )
