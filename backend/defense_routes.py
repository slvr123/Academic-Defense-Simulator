"""POST /sessions/{id}/start, POST /sessions/{id}/answers, GET /sessions/{id}/report,
and POST /tts.

Plain `def` routes: v1's engine and v1's MiMo client are synchronous, so FastAPI
runs each in its threadpool and the event loop stays free for every other request.
Start, answers and report take the session's turn lock without waiting; a second
request for the same session while one is running gets 409, not a queue. /tts never
takes it, so a voice playing never blocks an answer (M3b E5).

Answers are idempotent when the client says which turn it answers (M3b E13): a
resend of the turn just answered gets that turn's stored response again, with no
re-scoring, and any other turn but the open one gets 409 `stale_question`. /start
is too (M6 H4): a resend before the first answer gets the original response, and
after it 409 `already_started`. GET /current says where the defense is (M6 H5).

Quota: /start reserves one slot for today (Pacific) before any Gemini call, and
fails closed (503) if the store can't be reached. Nothing else touches it. A
Gemini 429 or surviving 503 anywhere is "busy" (decisions doc, item 3).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response

from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError
from backend.client_ip import StartLimiter, client_ip
from backend.defense import (
    AnsweredTurn,
    Defense,
    current_question,
    Providers,
    ProvidersFactory,
    answer,
    ask,
    evaluation_out,
    panelist_out,
    setup_defense,
)
from backend.errors import CodedHTTPException
from backend.llm_errors import PANEL_BUSY_MESSAGE, PANEL_BUSY_RETRY_AFTER_S, is_panel_busy
from backend.quota import QuotaPlan, QuotaUnavailable, pacific_day
from backend.schemas import (
    AnswerRequest,
    AnswerResponse,
    ChunkCopy,
    CurrentQuestion,
    ErrorBody,
    ReportResponse,
    StartResponse,
    TranscriptTurn,
    TtsRequest,
)
from backend.sessions import Session, SessionStatus, SessionStore
from backend.settings import Settings
from backend.tts import Synthesize, TtsFailed, synthesize_wav
from backend.tts_split import split_sentences

logger = logging.getLogger("backend.defense")

NOT_READY_MESSAGE = "The document is still being read. Start the defense once its status is ready."
FAILED_MESSAGE = "This document couldn't be read, so there is no defense to start. Upload it again."
ALREADY_STARTED_MESSAGE = "This defense has already started. Answer the current question instead."
NOT_STARTED_MESSAGE = "This defense hasn't started yet."
DEFENSE_DONE_MESSAGE = "This defense is finished. Its report is ready."
REPORT_NOT_READY_MESSAGE = "The report is ready once the last answer is scored."
TURN_IN_PROGRESS_MESSAGE = "The panel is still working on this session's last request. Wait for it to finish."
PANEL_ERROR_MESSAGE = "The panel hit a problem with that turn. Nothing was recorded; send the same request again."
QUOTA_DOWN_MESSAGE = "The demo's usage counter can't be reached right now, so no defense can start. Try again shortly."
STALE_QUESTION_MESSAGE = (
    "That answer is for a question that isn't open any more. The defense has moved on without it."
)
QUESTION_NOT_FOUND_MESSAGE = "That question isn't part of this session."
TTS_UNAVAILABLE_MESSAGE = "Panel voices aren't set up on this server."
TTS_LIMIT_MESSAGE = "This question has already been voiced as often as it can be."
TTS_FAILED_MESSAGE = "The panel voice couldn't be made for this question."


def per_ip_message(limit: int) -> str:
    plural = "s" if limit != 1 else ""
    return f"This network has already started {limit} demo defense{plural} today. More open at midnight Pacific time."

Clock = Callable[[], datetime]


def gemini_providers_factory(settings: Settings) -> ProvidersFactory:
    """The real providers, one pair per defense. Fails at startup without a key."""
    if settings.gemini_api_key is None:
        raise RuntimeError("GEMINI_API_KEY is not set; defenses need it")
    key = settings.gemini_api_key.get_secret_value()

    def build(calls: CallCounter) -> Providers:
        def provider(model: str, label: str) -> GeminiProvider:
            return GeminiProvider(
                key, model, call_counter=calls, label=label, rpm_ceiling=settings.rpm_ceiling(model)
            )

        return Providers(
            defense=provider(settings.gemini_model, settings.gemini_model),
            suggestions=provider(settings.gemini_suggestions_model, settings.gemini_suggestions_model),
        )

    return build


def llm_failure(exc: LLMProviderError) -> HTTPException:
    if is_panel_busy(exc):
        return HTTPException(503, PANEL_BUSY_MESSAGE, headers={"Retry-After": str(PANEL_BUSY_RETRY_AFTER_S)})
    return HTTPException(502, PANEL_ERROR_MESSAGE)


@contextmanager
def _turn(session: Session) -> Iterator[None]:
    if not session.turn_lock.acquire(blocking=False):
        raise HTTPException(409, TURN_IN_PROGRESS_MESSAGE)
    try:
        yield
    finally:
        session.turn_lock.release()


def register_defense_routes(
    app: FastAPI,
    *,
    settings: Settings,
    quota_plan: QuotaPlan,
    clock: Clock,
    not_found: Callable[[], HTTPException],
    quota_used_up: Callable[..., HTTPException],
    synthesize: Synthesize,
) -> None:
    def ready_session(session_id: str) -> Session:
        store: SessionStore = app.state.store
        session = store.get(session_id)
        if session is None:
            raise not_found()
        if session.status is SessionStatus.FAILED:
            raise HTTPException(409, FAILED_MESSAGE)
        if session.status is not SessionStatus.READY:
            raise HTTPException(409, NOT_READY_MESSAGE)
        return session

    limiter = StartLimiter(settings.per_ip_daily_starts)

    def reserve_quota(ip: str) -> None:
        """The per-IP limit first, then today's slot. A start the quota refuses
        doesn't count against the IP."""
        now = clock()
        day = pacific_day(now)
        if not limiter.take(day, ip):
            logger.info("per-ip limit reached ip=%s", ip)
            raise quota_used_up(
                now, per_ip_message(settings.per_ip_daily_starts), "per_ip_limit", limit=settings.per_ip_daily_starts
            )
        try:
            reserved = app.state.quota.reserve(day, quota_plan.cap)
        except QuotaUnavailable as exc:
            limiter.give_back(day, ip)
            logger.warning("defense start refused, quota store unreachable: %s", exc)
            raise HTTPException(503, QUOTA_DOWN_MESSAGE) from exc
        if reserved is None:
            limiter.give_back(day, ip)
            raise quota_used_up(now)
        logger.info("quota reserved %d/%d for %s", reserved, quota_plan.cap, day)

    @app.post(
        "/sessions/{session_id}/start", response_model=StartResponse,
        responses={409: {"model": ErrorBody}, 429: {"model": ErrorBody}},
    )
    def start_defense(session_id: str, request: Request) -> StartResponse:
        session = ready_session(session_id)
        with _turn(session):
            defense = session.defense
            if defense is not None and (defense.pending is not None or defense.done):
                # A resend whose first response was lost (H4): the same body, no Gemini
                # call and no second reservation, until an answer has gone in.
                if defense.start_response is not None and not defense.session.turns and defense.last_answer is None:
                    logger.info("start replayed session=%s", session_id)
                    return defense.start_response
                raise CodedHTTPException(409, ALREADY_STARTED_MESSAGE, "already_started")
            if defense is None:
                domain_count = len(session.panel.domain_archetypes())
                if len(session.chunks) < domain_count:
                    raise HTTPException(
                        422,
                        f"This document has {len(session.chunks)} passage(s), too few for {domain_count} "
                        "panelists to each open a topic. Choose fewer panelists or upload a longer document.",
                    )
                reserve_quota(client_ip(request, settings.trusted_proxy_hops))
                calls = CallCounter()
                defense = setup_defense(
                    session, app.state.providers_factory(calls), calls, app.state.embedder, settings.gemini_model
                )
                session.defense = defense
                app.state.last_chunk_copy = ChunkCopy(
                    session_id=session.id, chunks=len(defense.chunks), mb=round(defense.chunk_copy_bytes / 2**20, 3)
                )
            # A defense whose first question failed resumes here, without a new reservation.
            try:
                question = ask(defense)
            except LLMProviderError as exc:
                logger.warning("first question failed session=%s busy=%s: %s", session_id, is_panel_busy(exc), exc)
                raise llm_failure(exc) from exc
            profile = defense.session.profile
            defense.start_response = StartResponse(
                domain=profile.domain,
                topic=profile.topic,
                panel=[panelist_out(defense.session, p.archetype_key) for p in defense.session.panel],
                question=question,
            )
            return defense.start_response

    @app.get("/sessions/{session_id}/current", response_model=CurrentQuestion, responses={409: {"model": ErrorBody}})
    def get_current(session_id: str) -> CurrentQuestion:
        """The open question, or done (M6 H5). Under the turn lock, so it never reads a
        defense halfway through moving on; a turn in progress is 409, as elsewhere."""
        session = ready_session(session_id)
        with _turn(session):
            defense = _started(session)
            if defense.done:
                return CurrentQuestion(done=True, question=None)
            question = current_question(defense)
            if question is None:  # the first question failed; /start asks it again
                raise HTTPException(409, NOT_STARTED_MESSAGE)
            return CurrentQuestion(done=False, question=question)

    @app.post("/sessions/{session_id}/answers", response_model=AnswerResponse, responses={409: {"model": ErrorBody}})
    def submit_answer(session_id: str, body: AnswerRequest) -> AnswerResponse:
        session = ready_session(session_id)
        with _turn(session):
            defense = _started(session)
            # Before the done check, so a lost response to the final answer replays too.
            last = defense.last_answer
            if body.turn is not None and last is not None and body.turn == last.turn:
                logger.info("answer replayed session=%s turn=%d", session_id, body.turn)
                return last.response
            if defense.done:
                raise HTTPException(409, DEFENSE_DONE_MESSAGE)
            if defense.pending is None:  # the first question failed; /start asks it again
                raise HTTPException(409, NOT_STARTED_MESSAGE)
            turn = defense.open_turn
            assert turn is not None  # pending is set
            if body.turn is not None and body.turn != turn:
                raise CodedHTTPException(409, STALE_QUESTION_MESSAGE, "stale_question")
            try:
                result = answer(defense, body.text)
            except LLMProviderError as exc:
                logger.warning("turn failed session=%s busy=%s: %s", session_id, is_panel_busy(exc), exc)
                raise llm_failure(exc) from exc
            response = AnswerResponse(evaluation=result.evaluation, next_question=result.next_question, done=defense.done)
            defense.last_answer = AnsweredTurn(turn=turn, response=response)
        return response

    @app.post(
        "/tts",
        response_class=Response,
        responses={
            200: {"content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}},
            404: {"model": ErrorBody},
            429: {"model": ErrorBody},
            502: {"model": ErrorBody},
            503: {"model": ErrorBody},
        },
    )
    def tts(body: TtsRequest) -> Response:
        """One question's audio as WAV bytes, in the asking panelist's voice. The
        server keeps none of it once the response is sent. With `part`, one sentence
        of it and an X-TTS-Parts header (M6 H9); while ADS_TTS_SPLIT_SENTENCES is off
        the whole question is the only part, so a client asking for parts still works."""
        key = settings.mimo_key
        if key is None:
            raise CodedHTTPException(503, TTS_UNAVAILABLE_MESSAGE, "tts_unavailable")
        store: SessionStore = app.state.store
        session = store.get(body.session_id)
        if session is None:
            raise not_found()
        defense = session.defense
        asked = defense.asked.get(body.turn) if defense is not None else None
        if defense is None or asked is None:
            raise HTTPException(404, QUESTION_NOT_FOUND_MESSAGE)
        parts = [asked.text]
        if body.part is not None and settings.tts_split_sentences:
            parts = split_sentences(asked.text)
        if body.part is not None and body.part >= len(parts):
            raise HTTPException(404, QUESTION_NOT_FOUND_MESSAGE)
        text = parts[body.part or 0]
        if not defense.take_tts_call(body.turn, body.part):
            raise CodedHTTPException(429, TTS_LIMIT_MESSAGE, "tts_limit")
        started = time.perf_counter()
        # Timings, sizes and positions only: the question's text is never logged (H8).
        part_label = "-" if body.part is None else f"{body.part}/{len(parts)}"
        try:
            audio = synthesize_wav(synthesize, text, asked.archetype_key, key)
        except TtsFailed as exc:
            logger.warning(
                "tts failed session=%s turn=%d part=%s chars=%d ms=%d: %s", body.session_id, body.turn,
                part_label, len(text), (time.perf_counter() - started) * 1000, exc,
            )
            raise CodedHTTPException(502, TTS_FAILED_MESSAGE, "tts_failed") from exc
        logger.info(
            "tts session=%s turn=%d part=%s archetype=%s chars=%d bytes=%d ms=%d", body.session_id, body.turn,
            part_label, asked.archetype_key, len(text), len(audio), (time.perf_counter() - started) * 1000,
        )
        headers = {"X-TTS-Parts": str(len(parts))} if body.part is not None else None
        return Response(audio, media_type="audio/wav", headers=headers)

    @app.get("/sessions/{session_id}/report", response_model=ReportResponse)
    def get_report(session_id: str) -> ReportResponse:
        session = ready_session(session_id)
        defense = _started(session)
        if not defense.done:
            raise HTTPException(409, REPORT_NOT_READY_MESSAGE)
        state = defense.session
        assert state.report is not None
        return ReportResponse(
            domain=state.profile.domain,
            topic=state.profile.topic,
            report=state.report,
            transcript=[
                TranscriptTurn(
                    turn=i + 1,
                    panelist=panelist_out(state, turn.panelist_archetype_key),
                    question=turn.question,
                    difficulty=turn.difficulty_level,
                    sources=[source],
                    answer=turn.answer or "",
                    evaluation=evaluation_out(turn),
                )
                for i, (turn, source) in enumerate(zip(state.turns, defense.sources, strict=True))
            ],
        )


def _started(session: Session) -> Defense:
    if session.defense is None:
        raise HTTPException(409, NOT_STARTED_MESSAGE)
    return session.defense


def defense_status(session: Session) -> str | None:
    if session.status is not SessionStatus.READY:
        return None
    if session.defense is None:
        return "not_started"
    return "done" if session.defense.done else "in_progress"
