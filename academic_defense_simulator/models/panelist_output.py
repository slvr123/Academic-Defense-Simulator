"""Panelist output schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PanelistQuestion(BaseModel):
    question: str
    grounding_reference: str
    difficulty_level: int = Field(..., ge=1, le=5)
