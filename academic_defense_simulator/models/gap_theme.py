"""Recurring primary_gap theme clustering output schema (v1.0b Decision 2)."""

from __future__ import annotations

from pydantic import BaseModel


class GapTheme(BaseModel):
    theme_label: str  # short human name, e.g. "Quantitative justification"
    supporting_gaps: list[str]  # the original primary_gap strings grouped here, verbatim
    occurrence_count: int


class GapThemeAnalysis(BaseModel):
    themes: list[GapTheme]
