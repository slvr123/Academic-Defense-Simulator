"""M12 Decision 1: the opening round walks forward through the paper.

v1's engine opens topics in the order the domain panelists sit in `session.panel`
(select_active_panelist rotates `domain_panelists[len(used_chunk_indices) % n]`), and
holds Devil's Advocate until every domain panelist has spoken. So ordering the seated
panel by each archetype's home chapter, before the panel reaches v1, orders the
opening round with no v1 change. After round one the engine's adaptive turn-taking
runs exactly as before: a panelist who finds a weakness still keeps the floor.

Only the seating order changes. Persona generation sees the roster in its usual
PANEL_COMPOSITION order, so its prompt input is unchanged.
"""

from __future__ import annotations

from typing import Final

from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY

# The chapter of a typical five-chapter paper each archetype's questions belong to.
HOME_CHAPTER: Final[dict[str, int]] = {
    "problem_objectives_reviewer": 1,
    "literature_theory_specialist": 2,
    "methodology_expert": 3,
    "technical_implementation_reviewer": 3,
    "statistical_analysis_reviewer": 3,
    "results_conclusions_reviewer": 4,
    "ethics_practicality_reviewer": 5,
    "industry_practice_reviewer": 5,
}


def opening_order(panel: list[Panelist]) -> list[Panelist]:
    """Domain panelists by home chapter, ascending; ties keep their relative order
    (sorted is stable); Devil's Advocate last. A key missing from HOME_CHAPTER raises
    KeyError, so a new archetype can't be seated without a home chapter."""
    domain = [p for p in panel if p.archetype_key != DEVILS_ADVOCATE_KEY]
    devils_advocate = [p for p in panel if p.archetype_key == DEVILS_ADVOCATE_KEY]
    return sorted(domain, key=lambda p: HOME_CHAPTER[p.archetype_key]) + devils_advocate
