"""Session state models — carried explicitly through the agent loop, no global state."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile
from academic_defense_simulator.models.panelist import Panelist


class ConversationTurn(BaseModel):
    panelist_archetype_key: str  # which panelist asked — round-robin means this varies turn to turn (v0.3b)
    panelist_name: str  # surname, denormalized off Panelist for digest rendering without a panel lookup
    question: str
    grounding_reference: str
    chunk_index: int
    chunk_text: str
    difficulty_level: int
    answer: Optional[str] = None
    score: Optional[AnswerScore] = None


class DefenseSession(BaseModel):
    profile: DefenseProfile
    panel: list[Panelist]  # full generated panel, composition order (0.3a Decision 5)
    difficulty_current: int
    turns: list[ConversationTurn] = Field(default_factory=list)

    @property
    def used_chunk_indices(self) -> set[int]:
        return {t.chunk_index for t in self.turns}

    @property
    def follow_ups_on_current_topic(self) -> int:
        """Consecutive follow-up turns already spent on the most recent chunk. Counts
        trailing turns sharing the latest turn's chunk_index, minus 1 for the new-topic
        turn that opened the chunk. 0 right after a fresh new-topic turn. Derived, not
        persisted — same approach as used_chunk_indices."""
        if not self.turns:
            return 0
        current_chunk = self.turns[-1].chunk_index
        trailing = 0
        for turn in reversed(self.turns):
            if turn.chunk_index != current_chunk:
                break
            trailing += 1
        return trailing - 1
