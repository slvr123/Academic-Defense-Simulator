"""Generated panelist persona models (0.3a persona generation)."""

from __future__ import annotations

from pydantic import BaseModel


class Panelist(BaseModel):
    archetype_key: str  # key into ARCHETYPE_CONFIG
    panelist_name: str  # surname only — template supplies "Dr.", per the Day 2 double-Dr. fix
    persona_framing: str  # 1-2 sentence character framing, injected into prompts


class PanelGeneration(BaseModel):
    """Structured-output schema for the persona-generation call."""

    panelists: list[Panelist]
