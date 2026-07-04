"""Session state models — carried explicitly through the agent loop, no global state."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile


class ConversationTurn(BaseModel):
    question: str
    grounding_reference: str
    chunk_index: int
    chunk_text: str
    difficulty_level: int
    answer: Optional[str] = None
    score: Optional[AnswerScore] = None


class DefenseSession(BaseModel):
    profile: DefenseProfile
    difficulty_current: int
    turns: list[ConversationTurn] = Field(default_factory=list)

    @property
    def used_chunk_indices(self) -> set[int]:
        return {t.chunk_index for t in self.turns}
