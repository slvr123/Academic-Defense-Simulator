"""One defense, driven through v1's engine: set up the panel, ask, score, report.

The routes own HTTP, the quota and the per-session lock; this module owns the
defense state and the order of engine calls, the same order v1's drivers use
(main.py, streamlit_app.py): extraction if needed, persona generation, then per
turn select_active_panelist, _generate_question, _score_answer,
record_scored_turn, and build_report once session_is_complete.

A turn either happens completely or not at all. `answer` works on a copy of the
session: the answer is scored, recorded, and the next question generated (or the
report built) on the copy, and only if every call succeeds does the copy replace
the defense's state. If a Gemini call fails, nothing changes and the client sends
the same answer again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath

from academic_defense_simulator.document_profile import extract_document_profile
from academic_defense_simulator.engine import (
    _generate_question,
    _score_answer,
    record_scored_turn,
    select_active_panelist,
    session_is_complete,
)
from academic_defense_simulator.llm.provider import CallCounter, LLMProvider
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.panel import compose_full_roster, generate_panel
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from academic_defense_simulator.rag.retrieval import Chunk
from academic_defense_simulator.report import build_report
from backend.embedder import Embedder
from backend.engine_adapter import RecordingEncoder, SourceTracker, chunk_copy_bytes, engine_chunks
from backend.schemas import Evaluation, PanelistOut, PanelConfig, QuestionOut, Source
from backend.sessions import Session

logger = logging.getLogger("backend.defense")

# Used when extraction can't read a domain from the document (decisions doc, item 6).
UNSPECIFIED_DOMAIN = "unspecified"


@dataclass(frozen=True)
class Providers:
    """The two Gemini roles a defense uses: everything on the defense model, answer
    suggestions on their own model (decisions doc, item 1)."""

    defense: LLMProvider
    suggestions: LLMProvider


# Built per defense, so the counter tallies that defense's calls alone.
ProvidersFactory = Callable[[CallCounter], Providers]


@dataclass
class Defense:
    session: DefenseSession
    chunks: list[Chunk]
    encoder: RecordingEncoder
    tracker: SourceTracker
    providers: Providers
    calls: CallCounter
    model: str  # the defense model's name, which v1's engine passes to its grounding check
    other_subtype_line: str
    pending: ConversationTurn | None = None  # asked, not yet answered
    pending_source: Source | None = None
    sources: list[Source] = field(default_factory=list)  # one per recorded turn

    @property
    def done(self) -> bool:
        return self.session.report is not None


def setup_defense(
    session: Session, providers: Providers, calls: CallCounter, embedder: Embedder, model: str
) -> Defense:
    """Extraction (only when the client sent no domain or topic) and persona
    generation. Neither raises on a Gemini failure: v1 falls back to blank
    fields and to the static panel, and so does this."""
    assert session.matrix is not None
    config: PanelConfig = session.panel
    domain, topic = config.domain, config.topic
    if domain is None or topic is None:
        extracted = extract_document_profile([c.text for c in session.chunks], providers.defense)
        domain = domain or extracted.domain.strip() or UNSPECIFIED_DOMAIN
        topic = topic or extracted.topic.strip() or PurePath(session.filename).stem
    profile = config.to_profile(domain=domain, topic=topic, document_id=session.id)
    panel, fallback_used = generate_panel(profile, compose_full_roster(profile), providers.defense)
    chunks = engine_chunks(session.matrix, session.chunks)
    logger.info(
        "defense setup session=%s panel=%s persona_fallback=%s calls=%d chunks=%d chunk_copy_mb=%.2f",
        session.id, [p.archetype_key for p in panel], fallback_used, calls.total, len(chunks),
        chunk_copy_bytes(chunks) / 2**20,
    )
    return Defense(
        session=DefenseSession(profile=profile, panel=panel, difficulty_current=profile.difficulty_start),
        chunks=chunks,
        encoder=RecordingEncoder(embedder),
        tracker=SourceTracker(session.matrix, session.chunks),
        providers=providers,
        calls=calls,
        model=model,
        other_subtype_line=(
            f"\n- Defense subtype: {profile.other_subtype.value}" if profile.other_subtype is not None else ""
        ),
    )


def ask(defense: Defense) -> QuestionOut:
    """Generates the next question and holds it as pending. Raises LLMProviderError
    with the defense unchanged."""
    turn, source = _next_question(defense, defense.session, defense.tracker)
    defense.pending, defense.pending_source = turn, source
    return question_out(defense.session, turn, source)


@dataclass(frozen=True)
class AnswerResult:
    evaluation: Evaluation
    next_question: QuestionOut | None


def answer(defense: Defense, text: str) -> AnswerResult:
    """Scores `text` against the pending question and moves the defense on: the
    next question, or the report when the defense is complete. All or nothing; on
    LLMProviderError the defense is exactly as before."""
    assert defense.pending is not None and defense.pending_source is not None
    working = defense.session.model_copy(deep=True)
    tracker = SourceTracker(defense.tracker.matrix, defense.tracker.chunks, dict(defense.tracker.first_scores))
    turn = defense.pending.model_copy(deep=True)
    turn.answer = text
    panelist = next(p for p in working.panel if p.archetype_key == turn.panelist_archetype_key)
    turn.score = _score_answer(defense.providers.defense, turn, panelist, working.profile.defense_type.value)
    record_scored_turn(working, turn)

    next_turn: ConversationTurn | None = None
    next_source: Source | None = None
    if session_is_complete(working):
        working.report = build_report(working, defense.providers.defense, defense.providers.suggestions)
    else:
        next_turn, next_source = _next_question(defense, working, tracker)

    # Every call succeeded: commit.
    defense.sources.append(defense.pending_source)
    defense.session, defense.tracker = working, tracker
    defense.pending, defense.pending_source = next_turn, next_source
    if defense.done:
        logger.info(
            "defense done session=%s turns=%d calls=%d by_role=%s",
            working.profile.document_id, len(working.turns), defense.calls.total, defense.calls.by_stage,
        )
    return AnswerResult(
        evaluation=evaluation_out(turn),
        next_question=question_out(working, next_turn, next_source) if next_turn and next_source else None,
    )


def _next_question(
    defense: Defense, session: DefenseSession, tracker: SourceTracker
) -> tuple[ConversationTurn, Source]:
    panelist = select_active_panelist(session)
    defense.encoder.take_query()  # drop a query left by an earlier failed attempt
    turn = _generate_question(
        defense.providers.defense, session, defense.chunks, defense.encoder, panelist,
        defense.other_subtype_line, defense.model,
    )
    return turn, tracker.source_for(turn, defense.encoder.take_query())


def panelist_out(session: DefenseSession, archetype_key: str) -> PanelistOut:
    panelist = next(p for p in session.panel if p.archetype_key == archetype_key)
    return PanelistOut(
        archetype_key=panelist.archetype_key,
        name=panelist.panelist_name,
        title=ARCHETYPE_CONFIG[panelist.archetype_key]["archetype_title"],
        icon=panelist.icon,
    )


def question_out(session: DefenseSession, turn: ConversationTurn, source: Source) -> QuestionOut:
    return QuestionOut(
        turn=len(session.turns) + 1,
        panelist=panelist_out(session, turn.panelist_archetype_key),
        question=turn.question,
        difficulty=turn.difficulty_level,
        sources=[source],
    )


def evaluation_out(turn: ConversationTurn) -> Evaluation:
    assert turn.score is not None
    score = turn.score
    return Evaluation(
        clarity=score.clarity,
        depth=score.depth,
        grounding=score.grounding,
        total=score.clarity + score.depth + score.grounding,
        difficulty_delta=score.difficulty_delta,
        primary_gap=score.primary_gap,
        answer_summary=score.answer_summary,
    )
