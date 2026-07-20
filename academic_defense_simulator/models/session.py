"""Session state models — carried explicitly through the agent loop, no global state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import DefenseReport


class ConversationTurn(BaseModel):
    panelist_archetype_key: str  # which panelist asked — round-robin means this varies turn to turn (v0.3b)
    panelist_name: str  # surname, denormalized off Panelist for digest rendering without a panel lookup
    question: str
    grounding_reference: str
    chunk_index: int
    chunk_text: str
    difficulty_level: int
    grounding_retry_used: bool = False  # first is_grounded() attempt failed at difficulty >= 4; retry issued
    grounding_flagged: bool = False  # both attempts failed; served anyway (v0.3 hardening Decision 4)
    answer: Optional[str] = None
    score: Optional[AnswerScore] = None


class DefenseSession(BaseModel):
    profile: DefenseProfile
    panel: list[Panelist]  # full generated panel, composition order (0.3a Decision 5)
    difficulty_current: int
    turns: list[ConversationTurn] = Field(default_factory=list)
    report: Optional[DefenseReport] = None  # populated by the engine at session end (0.3c)

    @property
    def used_chunk_indices(self) -> set[int]:
        return {t.chunk_index for t in self.turns}

    @property
    def follow_ups_on_current_topic(self) -> int:
        """Consecutive follow-up turns already spent on the current panelist's current
        chunk. Counts trailing turns sharing the latest turn's chunk_index AND its
        asking panelist (panelist_archetype_key), minus 1 for the new-topic turn that
        opened the chunk. 0 right after a fresh new-topic turn, and also 0 the moment a
        different panelist lands on a chunk someone else already exhausted — v0.3f's
        retention cap is a per-panelist floor, not a per-chunk one (v0.3i fix: see
        docs/v0.3h-i-da-retention-scope-fix.md; this was previously chunk-only, which
        let one panelist's exhausted chunk silently deny Devil's Advocate its own
        follow-up chain when DA's contested claim happened to reuse that chunk).
        Derived, not persisted — same approach as used_chunk_indices."""
        if not self.turns:
            return 0
        last_turn = self.turns[-1]
        current_chunk = last_turn.chunk_index
        current_panelist = last_turn.panelist_archetype_key
        trailing = 0
        for turn in reversed(self.turns):
            if turn.chunk_index != current_chunk or turn.panelist_archetype_key != current_panelist:
                break
            trailing += 1
        return trailing - 1


class SessionStage(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"


# v0.4b Decision 7 — stamped into every persisted file; the loader accepts this exact
# value and rejects anything else (schema versioning: stamp now, migrate never yet).
CURRENT_SCHEMA_VERSION = 1


class PersistedSession(BaseModel):
    """One JSON file's full contents (v0.4b Decision 2) — the persistence module's
    save/load unit. Extends the v0.2.5 export shape rather than forking a rival one:
    `session` carries the exact `DefenseSession` (profile, seated panel, transcript,
    difficulty, report) the export already serializes; `document_chunks` is the one
    genuinely new piece of state, since chunk embeddings are deliberately not
    persisted (Decision 2) — resume re-embeds this text locally instead.

    Live state (difficulty, current speaker/turn position, follow-up retention
    counters) is deliberately NOT duplicated here as separate fields — it's already
    fully derivable from `session.turns` + `session.panel` (see
    `select_active_panelist`, `DefenseSession.follow_ups_on_current_topic`), which is
    exactly the Day 3 stateless-rendering property Decision 5 relies on."""

    # Literal, not plain int (Decision 7): a file stamped with any other version
    # must fail Pydantic validation outright, so `persistence.list_sessions` can
    # distinguish "incompatible version" from generic corruption and name it in
    # the skip reason, rather than silently accepting a structurally-valid file
    # from a schema that hasn't been written yet.
    schema_version: Literal[CURRENT_SCHEMA_VERSION] = CURRENT_SCHEMA_VERSION
    session_id: str
    created_at: datetime
    updated_at: datetime
    stage: SessionStage
    session: DefenseSession
    document_chunks: list[str]
