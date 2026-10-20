"""POST /sessions/{id}/start, POST /sessions/{id}/answers, GET /sessions/{id}/report.

Plain `def` routes: v1's engine is synchronous, so FastAPI runs each in its
threadpool. Each takes the session's turn lock without waiting; a second request
for the same session while one is running gets 409, not a queue.

Quota: /start reserves one slot for today (Pacific) before any Gemini call, and
fails closed (503) if the store can't be reached. Nothing else touches it. A
Gemini 429 or surviving 503 anywhere is "busy" (decisions doc, item 3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException

from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError
from backend.defense import (
    Defense,
    Providers,
    ProvidersFactory,
    answer,
    ask,
    evaluation_out,
    panelist_out,
    setup_defense,
)
from backend.llm_errors import PANEL_BUSY_MESSAGE, PANEL_BUSY_RETRY_AFTER_S, is_panel_busy
from backend.quota import QuotaPlan, QuotaUnavailable, pacific_day
from backend.schemas import AnswerRequest, AnswerResponse, ReportResponse, StartResponse, TranscriptTurn
from backend.sessions import Session, SessionStatus, SessionStore
from backend.settings import Settings

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
    quota_used_up: Callable[[datetime], HTTPException],
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

    def reserve_quota() -> None:
        now = clock()
        try:
            reserved = app.state.quota.reserve(pacific_day(now), quota_plan.cap)
        except QuotaUnavailable as exc:
            logger.warning("defense start refused, quota store unreachable: %s", exc)
            raise HTTPException(503, QUOTA_DOWN_MESSAGE) from exc
        if reserved is None:
            raise quota_used_up(now)
        logger.info("quota reserved %d/%d for %s", reserved, quota_plan.cap, pacific_day(now))

    @app.post("/sessions/{session_id}/start", response_model=StartResponse)
    def start_defense(session_id: str) -> StartResponse:
        session = ready_session(session_id)
        with _turn(session):
            defense = session.defense
            if defense is not None and (defense.pending is not None or defense.done):
                raise HTTPException(409, ALREADY_STARTED_MESSAGE)
            if defense is None:
                domain_count = len(session.panel.domain_archetypes())
                if len(session.chunks) < domain_count:
                    raise HTTPException(
                        422,
                        f"This document has {len(session.chunks)} passage(s), too few for {domain_count} "
                        "panelists to each open a topic. Choose fewer panelists or upload a longer document.",
                    )
                reserve_quota()
                calls = CallCounter()
                defense = setup_defense(
                    session, app.state.providers_factory(calls), calls, app.state.embedder, settings.gemini_model
                )
                session.defense = defense
            # A defense whose first question failed resumes here, without a new reservation.
            try:
                question = ask(defense)
            except LLMProviderError as exc:
                logger.warning("first question failed session=%s busy=%s: %s", session_id, is_panel_busy(exc), exc)
                raise llm_failure(exc) from exc
        profile = defense.session.profile
        return StartResponse(
            domain=profile.domain,
            topic=profile.topic,
            panel=[panelist_out(defense.session, p.archetype_key) for p in defense.session.panel],
            question=question,
        )

    @app.post("/sessions/{session_id}/answers", response_model=AnswerResponse)
    def submit_answer(session_id: str, body: AnswerRequest) -> AnswerResponse:
        session = ready_session(session_id)
        with _turn(session):
            defense = _started(session)
            if defense.done:
                raise HTTPException(409, DEFENSE_DONE_MESSAGE)
            if defense.pending is None:  # the first question failed; /start asks it again
                raise HTTPException(409, NOT_STARTED_MESSAGE)
            try:
                result = answer(defense, body.text)
            except LLMProviderError as exc:
                logger.warning("turn failed session=%s busy=%s: %s", session_id, is_panel_busy(exc), exc)
                raise llm_failure(exc) from exc
        return AnswerResponse(evaluation=result.evaluation, next_question=result.next_question, done=defense.done)

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
