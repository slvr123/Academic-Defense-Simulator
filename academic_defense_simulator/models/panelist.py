"""Generated panelist persona models (0.3a persona generation, 0.3j customization)."""

from __future__ import annotations

from pydantic import BaseModel


class GeneratedPanelist(BaseModel):
    """LLM-facing persona fields only. This model IS the structured-output wire schema
    (via PanelGeneration → response_schema), and PERSONA_GENERATION_PROMPT pins this
    exact JSON shape — `icon` must never appear here (v0.3j Decision 5: zero
    prompt/schema changes)."""

    archetype_key: str  # key into ARCHETYPE_CONFIG
    panelist_name: str  # surname only — template supplies "Dr.", per the Day 2 double-Dr. fix
    persona_framing: str  # 1-2 sentence character framing, injected into prompts


class Panelist(BaseModel):
    """Seated-roster panelist: a GeneratedPanelist after v0.3j customization —
    name override applied, icon resolved (user choice or archetype default), so
    downstream rendering never handles a missing icon."""

    archetype_key: str  # key into ARCHETYPE_CONFIG
    panelist_name: str  # surname only — template supplies "Dr.", per the Day 2 double-Dr. fix
    persona_framing: str  # 1-2 sentence character framing, injected into prompts
    icon: str


class PanelGeneration(BaseModel):
    """Structured-output schema for the persona-generation call."""

    panelists: list[GeneratedPanelist]
