"""Worst-case LLM calls per defense session, by call role and by model.

A pure count, built from the constants that bound each loop (`engine.turn_cap`,
`engine.GROUNDING_ATTEMPTS_MAX`, `panel.PERSONA_GENERATION_ATTEMPTS`), so it can't
drift from the code. Roles, not model names, are the unit: which model serves a
role is a deployment setting, and `calls_per_model` applies it.

Counted: every call the code makes on purpose, including the grounding retry and
the persona retry after a malformed roster. Not counted: retries after an error
(the provider's retry-once, extraction's second attempt). Those only happen when a
call already failed, so a caller sizing a daily quota covers them with a margin.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from academic_defense_simulator.engine import GROUNDING_ATTEMPTS_MAX, turn_cap
from academic_defense_simulator.panel import PERSONA_GENERATION_ATTEMPTS

SCORING_CALLS_PER_TURN = 1


class CallRole(str, Enum):
    EXTRACTION = "extraction"  # domain/topic prefill from the document head
    PERSONA = "persona"  # the whole panel's personas, one call
    QUESTION = "question"  # each turn's question, incl. Devil's Advocate's
    SCORING = "scoring"  # each turn's answer score
    NARRATIVE = "narrative"  # the report write-up
    SUGGESTIONS = "suggestions"  # the report's per-turn answer suggestions
    RELEVANCE_CHECK = "relevance_check"  # "is this an academic document" on upload


def worst_case_calls(panel_size: int, *, extraction: bool, relevance_check: bool) -> dict[CallRole, int]:
    """The most calls one session with `panel_size` panelists (Devil's Advocate
    included) can make, per role. `extraction` / `relevance_check` say whether the
    caller runs those steps at all; a role it skips counts 0."""
    turns = turn_cap(panel_size)
    return {
        CallRole.EXTRACTION: 1 if extraction else 0,
        CallRole.PERSONA: PERSONA_GENERATION_ATTEMPTS,
        CallRole.QUESTION: turns * GROUNDING_ATTEMPTS_MAX,
        CallRole.SCORING: turns * SCORING_CALLS_PER_TURN,
        CallRole.NARRATIVE: 1,
        CallRole.SUGGESTIONS: 1,
        CallRole.RELEVANCE_CHECK: 1 if relevance_check else 0,
    }


def calls_per_model(role_calls: Mapping[CallRole, int], role_models: Mapping[CallRole, str]) -> dict[str, int]:
    """Sums `role_calls` by the model each role is assigned in `role_models`. Every
    role that makes calls needs a model; a role with 0 calls may be left out."""
    missing = [role.value for role, calls in role_calls.items() if calls and role not in role_models]
    if missing:
        raise ValueError(f"no model assigned to call role(s): {', '.join(missing)}")
    totals: dict[str, int] = {}
    for role, calls in role_calls.items():
        if calls:
            totals[role_models[role]] = totals.get(role_models[role], 0) + calls
    return totals
