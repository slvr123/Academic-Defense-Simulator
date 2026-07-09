"""Ingestion-time document-profile extraction (0.3a Decision 4).

Talks only to the `LLMProvider` abstraction — kept separate from `rag/chunking.py` so
that module stays pure RAG (chunking is a text-processing concern; extraction is an LLM
concern that happens to run at ingestion time). See CLAUDE.md's "RAG separate from LLM
logic" architecture rule.
"""

from __future__ import annotations

import logging

from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError
from academic_defense_simulator.models.document_profile_extraction import DocumentProfileExtraction
from academic_defense_simulator.prompts.extraction_prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

_EXTRACTION_INPUT_CHAR_CAP = 3000  # title/abstract/framing live at the front; no gain from more


def extract_document_profile(chunks: list[str], provider: LLMProvider) -> DocumentProfileExtraction:
    """One LLM call at ingestion, prefilling the domain/topic form fields (Decision 4).
    First 3 chunks concatenated, capped at ~3000 characters — title/abstract/framing
    live at the front of any academic document.

    Retries once, then falls back to blank fields (exactly as v0.2 — the user types
    them). Never blocks the session; a warning is logged on failure."""
    document_head = "\n\n".join(chunks[:3])[:_EXTRACTION_INPUT_CHAR_CAP]
    prompt = EXTRACTION_PROMPT.format(document_head=document_head)

    for attempt in (1, 2):
        try:
            return provider.generate_structured(prompt, DocumentProfileExtraction)
        except LLMProviderError as exc:
            logger.warning("Domain/topic extraction failed on attempt %d: %s", attempt, exc)

    logger.warning("Domain/topic extraction failed after retry — leaving domain/topic blank for user entry.")
    return DocumentProfileExtraction(domain="", topic="")
