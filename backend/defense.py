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
import threading
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
from academic_defense_simulator.models.report import DefenseReport
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.panel import compose_full_roster, generate_panel
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from academic_defense_simulator.rag.retrieval import Chunk
from academic_defense_simulator.report import build_report
from backend.embedder import Embedder
from backend.engine_adapter import RecordingEncoder, SourceTracker, chunk_copy_bytes, engine_chunks
from backend.highlight import highlight_span
from backend.retrieval_sampling import NearTieRetriever, turn_seed
from backend.schemas import AnswerResponse, Evaluation, PanelistOut, PanelConfig, QuestionOut, Source, StartResponse
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

# One synthesis and one retry per question (M3b E3), or per part of it (M6 H9).
TTS_CALLS_PER_QUESTION = 2


@dataclass(frozen=True)
class AskedQuestion:
    """What /tts speaks for a turn: the question as sent, and who asked it."""

    text: str
    archetype_key: str


@dataclass(frozen=True)
class AnsweredTurn:
    """The last answer's turn and the response it got, replayed if the client
    resends that turn because the response never reached it (M3b E13)."""

    turn: int
    response: AnswerResponse


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
    chunk_copy_bytes: int = 0  # what `chunks` costs in memory
    pending: ConversationTurn | None = None  # asked, not yet answered
    pending_source: Source | None = None
    sources: list[Source] = field(default_factory=list)  # one per recorded turn
    # Every question the client has been sent, by turn. Written when a question
    # becomes pending, never changed after, so /tts reads it without the turn lock.
    asked: dict[int, AskedQuestion] = field(default_factory=dict)
    last_answer: AnsweredTurn | None = None  # only the last; set under the turn lock
    # The /start response as first sent, replayed to a resent /start until the first
    # answer is in (M6 H4). Set under the turn lock.
    start_response: StartResponse | None = None
    # (turn, part) -> synth calls made; part None is the whole question.
    tts_calls: dict[tuple[int, int | None], int] = field(default_factory=dict)
    tts_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def done(self) -> bool:
        return self.session.report is not None

    @property
    def open_turn(self) -> int | None:
        """The pending question's turn, or None when none is pending."""
        return len(self.session.turns) + 1 if self.pending is not None else None

    def take_tts_call(self, turn: int, part: int | None = None) -> bool:
        """Counts one synth call for `turn` (and `part`); False once its calls are used
        up. Under its own lock, so two /tts requests can't both take the last one."""
        key = (turn, part)
        with self.tts_lock:
            made = self.tts_calls.get(key, 0)
            if made >= TTS_CALLS_PER_QUESTION:
                return False
            self.tts_calls[key] = made + 1
            return True


def setup_defense(
    session: Session,
    providers: Providers,
    calls: CallCounter,
    embedder: Embedder,
    model: str,
    *,
    attribution_notes: bool = False,
) -> Defense:
    """Extraction (only when the client sent no domain or topic) and persona
    generation. Neither raises on a Gemini failure: v1 falls back to blank
    fields and to the static panel, and so does this. `attribution_notes` is
    settings.attribution_notes (M7 J3)."""
    assert session.matrix is not None
    config: PanelConfig = session.panel
    domain, topic = config.domain, config.topic
    if domain is None or topic is None:
        extracted = extract_document_profile([c.text for c in session.chunks], providers.defense)
        domain = domain or extracted.domain.strip() or UNSPECIFIED_DOMAIN
        topic = topic or extracted.topic.strip() or PurePath(session.filename).stem
    profile = config.to_profile(domain=domain, topic=topic, document_id=session.id)
    panel, fallback_used = generate_panel(profile, compose_full_roster(profile), providers.defense)
    chunks = engine_chunks(session.matrix, session.chunks, attribution_notes=attribution_notes)
    copy_bytes = chunk_copy_bytes(chunks, session.chunks)
    logger.info(
        "defense setup session=%s panel=%s persona_fallback=%s calls=%d chunks=%d chunk_copy_mb=%.2f "
        "attribution_notes=%d",
        session.id, [p.archetype_key for p in panel], fallback_used, calls.total, len(chunks),
        copy_bytes / 2**20, sum(c.text is not m.text for c, m in zip(chunks, session.chunks)),
    )
    return Defense(
        session=DefenseSession(profile=profile, panel=panel, difficulty_current=profile.difficulty_start),
        chunks=chunks,
        chunk_copy_bytes=copy_bytes,
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
    question = question_out(defense.session, turn, source)
    _record_asked(defense, question, turn)
    return question


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
    next_question = question_out(working, next_turn, next_source) if next_turn and next_source else None
    if next_question is not None and next_turn is not None:
        _record_asked(defense, next_question, next_turn)
    return AnswerResult(evaluation=evaluation_out(turn), next_question=next_question)


def _record_asked(defense: Defense, question: QuestionOut, turn: ConversationTurn) -> None:
    defense.asked[question.turn] = AskedQuestion(text=turn.question, archetype_key=turn.panelist_archetype_key)


def _next_question(
    defense: Defense, session: DefenseSession, tracker: SourceTracker
) -> tuple[ConversationTurn, Source]:
    panelist = select_active_panelist(session)
    defense.encoder.take_query()  # drop a query left by an earlier failed attempt
    # M6 H3: a new topic picks among near-ties, seeded by (session, turn), so a retried
    # attempt and any replay of this defense land on the same chunk.
    retriever = NearTieRetriever(turn_seed(session.profile.document_id, len(session.turns) + 1))
    turn = _generate_question(
        defense.providers.defense, session, defense.chunks, defense.encoder, panelist,
        defense.other_subtype_line, defense.model, retriever=retriever,
    )
    source = tracker.source_for(turn, defense.encoder.take_query())
    return turn, source.model_copy(update={"highlight": highlight_span(turn.grounding_reference, source.text)})


def current_question(defense: Defense) -> QuestionOut | None:
    """The open question, as it was sent; None when none is pending."""
    if defense.pending is None or defense.pending_source is None:
        return None
    return question_out(defense.session, defense.pending, defense.pending_source)


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
        grounding_reference=turn.grounding_reference,
    )


def overall_raw(report: DefenseReport) -> float:
    """M8 K8's headline: the mean of the three dimension averages, on v1's 1-5 scale.
    Computed here, not in the client, so the number in the report is one the backend
    owns and tests; the client only rescales it to 100 (scoreDisplay), as for every
    other score."""
    return (report.overall_avg_clarity + report.overall_avg_depth + report.overall_avg_grounding) / 3


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
