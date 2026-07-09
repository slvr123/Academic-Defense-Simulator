"""Domain/topic auto-extraction output schema (0.3a ingestion-time extraction)."""

from __future__ import annotations

from pydantic import BaseModel


class DocumentProfileExtraction(BaseModel):
    domain: str  # academic field/discipline, e.g. "Software Engineering"
    topic: str  # short research title/summary, <= 15 words
