"""Ingestion-time document relevance gate (v0.3g Brief).

One structured-output LLM call, run after chunking and before embedding, judging
whether the uploaded document is plausible defense material. Soft gate, not a hard
block — the caller decides how to act on a negative assessment; this module only
produces the judgment. No `streamlit` import (see CLAUDE.md's I/O-boundary rule);
talks only to the `LLMProvider` abstraction, same shape as `document_profile.py`'s
`extract_document_profile`, so it takes the provider as a parameter rather than
constructing one — the caller is responsible for pinning it to gemini-2.5-flash
(Decision: this is a judgment task, and flash-lite is already on record as unfit for
judgment calls — inverted difficulty_delta, inflated clarity scores).
"""

from __future__ import annotations

import logging

from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError
from academic_defense_simulator.models.document_assessment import DocumentAssessment
from academic_defense_simulator.prompts.relevance_prompts import RELEVANCE_PROMPT

logger = logging.getLogger(__name__)

_HEAD_SAMPLE_COUNT = 3
_MIDDLE_SAMPLE_COUNT = 2
_ASSESSMENT_INPUT_CHAR_CAP = 4000  # sampled excerpts only, not the whole document

# Fail-open fallback (Decision 2): the gate erroring must never block a real upload.
_FAIL_OPEN_ASSESSMENT = DocumentAssessment(
    is_defense_material=True,
    document_kind="unknown",
    reason="Relevance assessment was unavailable; proceeding without a check.",
)


def _sample_chunks(chunks: list[str]) -> list[str]:
    """First 2-3 chunks plus 1-2 from the middle (design context: first-pages-only
    gets fooled by cover letters and templated front matter). Short documents (at or
    below the sample budget) use every chunk they have — never fails on a short doc,
    and a plain index union naturally de-duplicates any head/middle overlap."""
    n = len(chunks)
    if n <= _HEAD_SAMPLE_COUNT + _MIDDLE_SAMPLE_COUNT:
        return chunks

    head_indices = range(_HEAD_SAMPLE_COUNT)
    mid_start = n // 2
    middle_indices = range(mid_start, min(mid_start + _MIDDLE_SAMPLE_COUNT, n))
    indices = sorted(set(head_indices) | set(middle_indices))
    return [chunks[i] for i in indices]


def assess_document(chunks: list[str], provider: LLMProvider) -> DocumentAssessment:
    """Judges whether `chunks` (raw chunk text, pre-embedding) looks like plausible
    defense material. Never raises: a provider failure is logged and treated as a
    pass (Decision 2, fail open) — the caller always gets a `DocumentAssessment` back."""
    sampled = "\n\n---\n\n".join(_sample_chunks(chunks))[:_ASSESSMENT_INPUT_CHAR_CAP]
    prompt = RELEVANCE_PROMPT.format(sampled_text=sampled)

    try:
        return provider.generate_structured(prompt, DocumentAssessment)
    except LLMProviderError as exc:
        logger.warning("Document relevance assessment failed, failing open: %s", exc)
        return _FAIL_OPEN_ASSESSMENT
