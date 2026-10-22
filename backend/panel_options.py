"""GET /panel-options: v1's panel rosters, read straight from v1's own tables.

Nothing here calls a model. The rosters come from PANEL_COMPOSITION, titles from
ARCHETYPE_CONFIG, icons from archetype_default_icon(), the same sources /start's
PanelistOut uses, so the picker and the seated panel can't disagree.
"""

from __future__ import annotations

from academic_defense_simulator.models.defense_profile import DefenseType, OtherSubtype
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY, PANEL_COMPOSITION, archetype_default_icon
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from backend.schemas import MAX_DOMAIN_PANELISTS, ArchetypeOption, PanelConfig, PanelOptions, PanelRoster


def archetype_option(archetype_key: str) -> ArchetypeOption:
    return ArchetypeOption(
        archetype_key=archetype_key,
        title=ARCHETYPE_CONFIG[archetype_key]["archetype_title"],
        icon=archetype_default_icon(archetype_key),
    )


def _panel_types() -> list[PanelConfig]:
    """One PanelConfig per PANEL_COMPOSITION key: each defense type, with "other" split by subtype."""
    configs: list[PanelConfig] = []
    for defense_type in DefenseType:
        if defense_type is DefenseType.OTHER:
            configs.extend(PanelConfig(defense_type=defense_type, other_subtype=s) for s in OtherSubtype)
        else:
            configs.append(PanelConfig(defense_type=defense_type))
    return configs


def panel_options() -> PanelOptions:
    return PanelOptions(
        max_domain_panelists=MAX_DOMAIN_PANELISTS,
        devils_advocate=archetype_option(DEVILS_ADVOCATE_KEY),
        rosters=[
            PanelRoster(
                defense_type=config.defense_type,
                other_subtype=config.other_subtype,
                archetypes=[archetype_option(key) for key in PANEL_COMPOSITION[config.composition_key]],
            )
            for config in _panel_types()
        ],
    )
