"""Document relevance gate output schema (v0.3g Brief)."""

from __future__ import annotations

from pydantic import BaseModel


class DocumentAssessment(BaseModel):
    is_defense_material: bool
    document_kind: str  # e.g. "research paper", "resume", "invoice", "novel excerpt"
    reason: str  # one sentence, user-facing on rejection
