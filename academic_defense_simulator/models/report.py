"""End-of-session scoring report schema (v0.3c). See
`docs/v0.3c-scoring-report-decisions.md` Decision 2 for rationale — attached to the
session because the narrative is one-of-a-kind LLM text, not recomputable like the
rest of the report."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


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


class DefenseReport(BaseModel):
    difficulty_trajectory: list[int]  # per-turn difficulty_current, in turn order
    overall_avg_clarity: float
    overall_avg_depth: float
    overall_avg_grounding: float
    panelist_sections: list[PanelistReportSection]  # composition order
    pushback_events: list[PushbackEvent]
    narrative: Optional[str] = None
    narrative_fallback_used: bool = False
