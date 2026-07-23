"""End-of-session scoring report schema (v0.3c). See
`docs/v0.3c-scoring-report-decisions.md` Decision 2 for rationale — attached to the
session because the narrative is one-of-a-kind LLM text, not recomputable like the
rest of the report."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PushbackOutcome(str, Enum):
    RECOVERED = "recovered"
    HELD = "held"
    DETERIORATED = "deteriorated"


class PushbackEvent(BaseModel):
    turn_index: int  # the escalated-into turn (t)
    prior_turn_index: int  # t-1
    difficulty_from: int
    difficulty_to: int
    quality_sum_prior: int  # 3-15
    quality_sum_at: int  # 3-15
    outcome: PushbackOutcome


class PanelistReportSection(BaseModel):
    archetype_key: str
    panelist_name: str
    turns_taken: int
    avg_clarity: float
    avg_depth: float
    avg_grounding: float
    primary_gaps: list[str]  # every non-null gap this panelist surfaced, in order


class AnswerSuggestion(BaseModel):
    """Grounded per-answer suggestion (v1.0b-2 Decision 1) — what a stronger answer
    would have included, for one turn. Independent of and architecturally separate
    from the analytics module's GapTheme/GapThemeAnalysis (v1.0b): different data
    flow (one session, not many), different prompt version constant."""

    turn_index: int
    suggestion: str  # specific: what a stronger answer would have included
    grounding_reference: str  # cites back to the actual retrieved chunk — same
    # discipline the panelist prompts already enforce


class AnswerSuggestionList(BaseModel):
    """Structured-output wire schema for the answer-suggestions call (v1.0b-2 Task 3)
    — `generate_structured` requires a BaseModel, not a bare `list[...]`, same reason
    `PanelGeneration` wraps `list[GeneratedPanelist]`."""

    suggestions: list[AnswerSuggestion]


class DefenseReport(BaseModel):
    difficulty_trajectory: list[int]  # per-turn difficulty_current, in turn order
    overall_avg_clarity: float
    overall_avg_depth: float
    overall_avg_grounding: float
    panelist_sections: list[PanelistReportSection]  # composition order
    pushback_events: list[PushbackEvent]
    narrative: Optional[str] = None
    narrative_fallback_used: bool = False
    # v1.0b-2 Decision 1: additive only, no schema_version bump — both fields have
    # defaults, so pre-existing persisted files (predating this feature) parse
    # unchanged, carrying an empty list/False that correctly reflects they predate it.
    answer_suggestions: list[AnswerSuggestion] = Field(default_factory=list)
    suggestions_fallback_used: bool = False
