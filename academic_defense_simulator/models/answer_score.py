"""Scoring output schema for a candidate's answer."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AnswerScore(BaseModel):
    clarity: int = Field(..., ge=1, le=5, description="How clearly and directly the answer addressed the question")
    depth: int = Field(..., ge=1, le=5, description="Substantive reasoning shown, not just restating facts")
    grounding: int = Field(
        ..., ge=1, le=5, description="How accurately and specifically the answer engaged with the document's actual content"
    )
    difficulty_delta: int = Field(
        ..., ge=-1, le=1, description="-1 = ease up, 0 = hold steady, 1 = escalate for the next question"
    )
    primary_gap: Optional[str] = Field(
        None,
        description="The single most significant weakness or gap observed — feeds the v0.3 end-of-session report, so kept honest even for a strong answer. Null only if there is genuinely no material weakness.",
    )
    answer_summary: str = Field(
        ...,
        description="1-2 sentences summarizing what the candidate CLAIMED — the claim itself, not a judgment of it. Feeds the cross-panelist digest (v0.3b) and Devil's Advocate target selection.",
    )
