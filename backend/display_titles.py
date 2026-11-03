"""Display titles the v2 API shows in place of v1's archetype titles (M13 D0c).

v1's ARCHETYPE_CONFIG titles feed the prompts, so they can't change. This map only
renames what the API returns: the archetype key and every prompt stay as they are.
"Terror" is Filipino student slang for a notoriously strict professor.
"""

from __future__ import annotations

from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG

DISPLAY_TITLES: dict[str, str] = {DEVILS_ADVOCATE_KEY: "Terror Panelist"}


def display_title(archetype_key: str) -> str:
    """The title the API shows for `archetype_key`: the override, else v1's own."""
    return DISPLAY_TITLES.get(archetype_key, ARCHETYPE_CONFIG[archetype_key]["archetype_title"])
