"""Minimal Streamlit wrap — thin I/O layer over `engine.py`'s turn loop (v0.3b Task 6,
v0.3d defense-simulation UI). Reuses `engine.py`'s turn-loop helpers and
`DefenseSession` as-is; no changes to `retrieve()`, prompt templates, scoring, or
report logic. Flow (0.3a Decision 4): upload -> ingest -> extract (one LLM call,
cached per document_id) -> profile form (prefilled, editable) -> start session.
`panel_size` is a form control, capped at the composition list length for the selected
defense type/subtype (0.3a Decision 1); `difficulty_start` stays hidden/hardcoded
(Pydantic default on `DefenseProfile`).

v0.3d (docs/v0.3d-defense-ui-decisions.md) adds the panelist cards, transcript-block
exchange rendering (Decision 8), the case-file sidebar (Decision 9), the dev-view
toggle, stage-keyed aborted-state copy, and the report view — presentation only,
zero business-logic changes. Palette is Decision 7 (re-revised), the final revision
before deploy; see that entry for the locked values and their rationale.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit (local `streamlit run` and Community Cloud alike) puts this script's own
# directory on sys.path, not the repo root — so `academic_defense_simulator.*` imports
# below fail with ModuleNotFoundError unless the repo root is added explicitly here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import concurrent.futures
import html
import json
import logging
import os
import tempfile
import threading
import time
from uuid import uuid4

import streamlit as st

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.document_profile import extract_document_profile
from academic_defense_simulator.engine import (
    DEFAULT_CALL_DELAY,
    MAX_TURNS,
    MODEL_CALL_DELAY_SECONDS,
    _clamp_difficulty,
    _generate_question,
    _score_answer,
    select_active_panelist,
)
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import DefenseReport
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import (
    DEVILS_ADVOCATE_KEY,
    PANEL_COMPOSITION,
    compose_full_roster,
    generate_panel,
)
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PROMPT_VERSION
from academic_defense_simulator.rag.chunking import DocumentIngestionError, chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk
from academic_defense_simulator.report import build_report

# Thin permanent call-lifecycle logging (v0.3 hardening, Task 1b) — light enough to ship,
# enough that a future hang recurrence has something to look at. Guarded the same way as
# `_turn_lock` below: Streamlit re-executes this module's top-level code on every rerun
# against the same module namespace, so `basicConfig` must only run once.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# Tripwire against infinite silence (v0.3 hardening, Task 1a) — order-of-magnitude, not a
# latency SLO. A stuck LLM call surfaces a clean "aborted" state instead of a silent
# spinner. The background thread is deliberately abandoned (not joined) on timeout so the
# driver can move on immediately rather than blocking on a call that may never return.
LLM_CALL_TIMEOUT_SECONDS = 120


def _call_with_timeout(fn, *args, label: str, **kwargs):
    st.session_state.call_counter = st.session_state.get("call_counter", 0) + 1
    call_id = st.session_state.call_counter
    thread_id = threading.get_ident()
    logger.info("call #%d (%s) started — thread %s", call_id, label, thread_id)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args, **kwargs)
    try:
        result = future.result(timeout=LLM_CALL_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        logger.info(
            "call #%d (%s) timed out after %ss — thread %s", call_id, label, LLM_CALL_TIMEOUT_SECONDS, thread_id
        )
        executor.shutdown(wait=False)
        raise LLMProviderError(
            f"The {label} call did not return within {LLM_CALL_TIMEOUT_SECONDS}s — treating as a stuck request."
        ) from exc
    executor.shutdown(wait=False)
    logger.info("call #%d (%s) returned — thread %s", call_id, label, thread_id)
    return result


# Stage-keyed abort copy (v0.3d Decision 5) — the user never sees raw exception text;
# it goes to logs (via `_abort` below) and the dev-view sidebar only. Keys are the exact
# `label` values already passed to `_call_with_timeout` at each of the three call sites
# — they were already mutually distinguishing, so no separate stage-key mechanism was
# needed (brief's "reuse the existing label if it's already distinguishing" note).
ABORT_MESSAGES = {
    "question generation": "The panel's next question took longer than expected.",
    "answer scoring": "Scoring your answer took longer than expected.",
    "report narrative": "Building your report took longer than expected.",
}


def _abort(label: str, exc: LLMProviderError) -> None:
    logger.error("session aborted at stage %r: %s", label, exc)
    st.session_state.stage = "aborted"
    st.session_state.abort_stage = label
    st.session_state.abort_message = str(exc)


st.set_page_config(page_title="Academic Defense Simulator", initial_sidebar_state="expanded")


def _inject_theme_css() -> None:
    """Dark prototype aesthetic, oxblood accent, gold garnish (v0.3d Decision 7
    re-revised — final palette before deploy). `config.toml` covers base theme,
    backgrounds, text, borders, and the primary accent; this covers what it can't —
    serif display headings, small-caps label tracking (including the gold-only
    sidebar-section variant), the transcript-block/answer-inset structure
    (Decision 8), the speaking-state badge and card avatars, and button/pill accent
    states. Gold (#D2A24C) is a garnish only — sidebar section labels, the speaking
    badge's outline, fine rules — never a CTA or fill color; that role stays
    oxblood (#8C3A3F) throughout."""
    st.markdown(
        """
        <style>
        h1, h2, h3, h4 {
            font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
            letter-spacing: 0.01em;
        }
        .small-caps-label {
            font-variant: small-caps;
            letter-spacing: 0.09em;
            font-size: 0.78rem;
            color: #8A8378;
        }
        .sidebar-section-label {
            font-variant: small-caps;
            letter-spacing: 0.09em;
            font-size: 0.78rem;
            color: #D2A24C;
            margin-top: 0.6rem;
        }
        [data-testid="stExpander"] summary p {
            font-variant: small-caps;
            letter-spacing: 0.09em;
            color: #ECE7DD;
        }

        /* Primary CTAs (Decision 7 re-revised: oxblood accent, on-accent text) */
        button[data-testid="stBaseButton-primary"] {
            background-color: #8C3A3F !important;
            border-color: #8C3A3F !important;
            color: #F2E9E4 !important;
        }
        button[data-testid="stBaseButton-primary"]:hover {
            background-color: #A3453F !important;
            border-color: #A3453F !important;
        }

        /* Active defense-type pill: solid accent fill, same treatment as a CTA */
        div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-pillsActive"] {
            background-color: #8C3A3F !important;
            border-color: #8C3A3F !important;
            color: #F2E9E4 !important;
        }

        .ads-card {
            border: 1px solid #2E2A25;
            border-radius: 12px;
            padding: 0.85rem 1rem;
            background: #1A1714;
            margin-bottom: 0.5rem;
            text-align: center;
        }
        .ads-card.speaking {
            border: 2px solid #8C3A3F;
            box-shadow: 0 0 0 2px rgba(140, 58, 63, 0.22);
        }
        .ads-card.completed {
            border-color: #6B655E;
        }
        .ads-card.waiting {
            opacity: 0.55;
        }
        .ads-avatar {
            width: 42px;
            height: 42px;
            border-radius: 50%;
            margin: 0 auto 0.4rem auto;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            color: #ECE7DD;
            background: repeating-linear-gradient(
                135deg, #8C3A3F, #8C3A3F 6px, #6E2C30 6px, #6E2C30 12px
            );
        }
        .ads-avatar.da {
            background: #8C3A3F;
        }
        .ads-card-name {
            font-family: Georgia, serif;
            font-weight: 600;
            color: #ECE7DD;
        }
        /* Card archetype labels: tracked tighter and smaller than section labels
           (0.62rem / 0.06em) so long titles don't break mid-word. */
        .ads-card-title.small-caps-label {
            margin-top: 0.15rem;
            letter-spacing: 0.06em;
            font-size: 0.62rem;
        }
        .ads-card-status {
            margin-top: 0.35rem;
            font-size: 0.82rem;
            color: #B5AEA2;
        }

        /* Speaking badge: gold outline only (garnish), accent-tinted text — gold
           never carries a CTA or fill role (Decision 7 re-revised, accent call). */
        .ads-speaking-badge {
            display: inline-block;
            margin-top: 0.35rem;
            padding: 0.1rem 0.55rem;
            border: 1px solid #D2A24C;
            border-radius: 999px;
            font-variant: small-caps;
            letter-spacing: 0.09em;
            font-size: 0.72rem;
            color: #C96F6F;
        }

        /* Transcript blocks (Decision 8) */
        .ads-turn-header {
            font-variant: small-caps;
            letter-spacing: 0.09em;
            font-size: 0.85rem;
            color: #ECE7DD;
            margin-bottom: 0.5rem;
        }
        .ads-answer-inset {
            margin-top: 0.75rem;
            padding: 0.65rem 0.9rem;
            background: #141210;
            border-left: 3px solid #2E2A25;
            border-radius: 0 8px 8px 0;
        }
        .ads-answer-label {
            font-variant: small-caps;
            letter-spacing: 0.09em;
            font-size: 0.72rem;
            color: #8A8378;
            margin-bottom: 0.25rem;
        }

        /* Active turn block (Decision 8): a real st.container(border=True, key=
           "active_turn_block"), so the accent border wraps the question AND the
           answer box in one DOM container, not just visually adjacent elements. */
        .st-key-active_turn_block {
            border: 2px solid #8C3A3F !important;
            box-shadow: 0 0 0 2px rgba(140, 58, 63, 0.18);
        }

        /* Intake dropzone (Task 1) — restyles the native file_uploader's own
           dropzone chrome; the widget's drag/drop and browse behavior are
           untouched, only its container border/background change. */
        [data-testid="stFileUploaderDropzone"] {
            border: 1px dashed rgba(140, 58, 63, 0.45) !important;
            border-radius: 12px !important;
            background: repeating-linear-gradient(
                -45deg, rgba(255,255,255,0.015) 0 10px, transparent 10px 20px
            ) !important;
            min-height: 108px !important;
            padding: 28px !important;
            align-items: center !important;
            justify-content: center !important;
        }

        /* Panel-composition preview cards (Task 1) — same .ads-card language as
           the live session's panel row, no state modifier (no session exists
           yet), placeholder name slot dimmed to read clearly as a placeholder. */
        .ads-card-name.ads-placeholder {
            opacity: 0.6;
            font-style: italic;
            font-size: 0.85rem;
            font-weight: 500;
        }

        /* Preview-card row (bug fix): flex-wrap with a real min-width per card,
           instead of st.columns dividing into `len(cards)` equal, floor-less
           slots — the cause of the illegible mid-word-wrapped cards. */
        .ads-preview-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
        }
        .ads-preview-row .ads-card {
            flex: 1 1 165px;
            min-width: 165px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_theme_css()

if "stage" not in st.session_state:
    st.session_state.stage = "intake"


def _render_hero() -> None:
    """Intake hero (presentation only) — eyebrow, serif title, muted tagline.
    Matches the design prototype's composition under the current oxblood-on-dark
    palette (Decision 7 re-revised), not the prototype's original clay-red. Shown
    only on the intake stage — the running/aborted/done views have their own
    headings and don't need the tagline repeated."""
    st.markdown(
        '<p class="small-caps-label" style="text-align:center;">The panel is waiting</p>'
        '<h1 style="text-align:center;">Academic Defense Simulator</h1>'
        '<p style="text-align:center;color:#B5AEA2;max-width:600px;margin:0 auto 1.5rem;">'
        "Upload your research. Face a panel that has actually read it — and gets "
        "harder when your answers get vague.</p>",
        unsafe_allow_html=True,
    )


if st.session_state.stage == "intake":
    _render_hero()
else:
    st.title("Academic Defense Simulator")

# A long blocking LLM call + time.sleep() inside a script run gives Streamlit's cooperative
# rerun-cancellation no checkpoint to interrupt at, so a second rerun for the same session
# can start concurrently with one still in flight (observed live: two overlapping
# _generate_question calls racing on st.session_state and deadlocking Streamlit's own
# session-state machinery). A real threading.Lock with double-checked locking closes that
# race regardless of any session-state visibility timing between the two threads — a plain
# session_state boolean flag was tried first and was not sufficient. Streamlit re-executes
# this module's top-level code on every rerun against the same module namespace, so the
# `not in globals()` guard is required — otherwise every rerun would allocate a fresh Lock,
# defeating the point of sharing one across concurrent reruns.
if "_turn_lock" not in globals():
    _turn_lock = threading.Lock()


def _reset() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.stage = "intake"


def _composition_key(defense_type: DefenseType, other_subtype: OtherSubtype | None) -> str:
    if defense_type == DefenseType.OTHER:
        assert other_subtype is not None
        return f"other/{other_subtype.value}"
    return defense_type.value


def _new_provider() -> GeminiProvider:
    """A fresh GeminiProvider (and its underlying HTTP client) per call, rather than one
    reused across reruns via st.session_state. Streamlit's script-runner executes each
    rerun on a new thread, and reusing a single genai.Client's connection pool across
    threads was observed to hang indefinitely on a later call in the same session."""
    settings = load_settings()
    return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)


def _archetype_title(archetype_key: str) -> str:
    return ARCHETYPE_CONFIG[archetype_key]["archetype_title"]


def _render_dev_view() -> None:
    """Diagnostics drawer (v0.3d Decision 4): every internal signal about the current
    session in one place, invisible unless the toggle is on. Deliberately in the
    sidebar, not the main flow — off means literally nothing extra renders; on means
    it renders here, never inline with the live turn/report views."""
    with st.sidebar:
        dev_view = st.toggle("Developer view", key="dev_view_toggle")
        if not dev_view:
            return
        st.caption(f"PROMPT_VERSION: {PROMPT_VERSION}")
        session: DefenseSession | None = st.session_state.get("session")
        if session is not None:
            st.caption(f"difficulty_current: {session.difficulty_current}/5")
            for i, turn in enumerate(session.turns, start=1):
                with st.expander(f"Turn {i} — Dr. {turn.panelist_name}"):
                    st.write(f"difficulty_level: {turn.difficulty_level}")
                    st.write(f"grounding_retry_used: {turn.grounding_retry_used}")
                    st.write(f"grounding_flagged: {turn.grounding_flagged}")
                    if turn.score is not None:
                        st.json(turn.score.model_dump())
        if st.session_state.get("abort_message"):
            st.caption("Last abort — raw exception text:")
            st.code(st.session_state.abort_message)


def _panelist_card_state(panelist: Panelist, session: DefenseSession, active_panelist: Panelist) -> tuple[str, str]:
    """(state, status_line) for one panelist card — v0.3d Decision 2. Speaking is an
    archetype_key match against the round-robin's active panelist (imported from
    engine.py, not reimplemented); completed/waiting are derived from `session.turns`
    with no new fields."""
    if panelist.archetype_key == active_panelist.archetype_key:
        return "speaking", "Speaking now"
    turns_taken = sum(1 for t in session.turns if t.panelist_archetype_key == panelist.archetype_key)
    if turns_taken > 0:
        noun = "question" if turns_taken == 1 else "questions"
        return "completed", f"{turns_taken} {noun} asked"
    if panelist.archetype_key == DEVILS_ADVOCATE_KEY:
        return "waiting", "Speaks last"
    return "waiting", "Waiting"


def _render_panelist_card(panelist: Panelist, state: str, status_line: str) -> str:
    initial = html.escape(panelist.panelist_name[:1].upper())
    name = html.escape(f"Dr. {panelist.panelist_name}")
    title = html.escape(_archetype_title(panelist.archetype_key))
    status = html.escape(status_line)
    avatar_class = "ads-avatar da" if panelist.archetype_key == DEVILS_ADVOCATE_KEY else "ads-avatar"
    # Speaking state gets the gold-outlined badge (Decision 7 re-revised, accent call —
    # gold is a garnish only); completed/waiting keep the plain muted status line.
    status_html = (
        f'<div class="ads-speaking-badge">{status}</div>'
        if state == "speaking"
        else f'<div class="ads-card-status">{status}</div>'
    )
    return (
        f'<div class="ads-card {state}">'
        f'<div class="{avatar_class}">{initial}</div>'
        f'<div class="ads-card-name">{name}</div>'
        f'<div class="ads-card-title small-caps-label">{title}</div>'
        f"{status_html}"
        f"</div>"
    )


def _render_panel_row(session: DefenseSession, active_panelist: Panelist) -> None:
    """The card row IS the speaking-order legend (Decision 1) — `session.panel` is
    already composition order, which is round-robin speaking order."""
    cols = st.columns(len(session.panel))
    for col, panelist in zip(cols, session.panel):
        state, status_line = _panelist_card_state(panelist, session, active_panelist)
        with col:
            st.markdown(_render_panelist_card(panelist, state, status_line), unsafe_allow_html=True)


def _render_panel_preview_card(archetype_key: str) -> str:
    """Panel-composition preview for the intake screen (Task 1, presentation
    only) — same `.ads-card` visual language as the live session's panel row, but
    no name exists yet (persona generation hasn't run), so the name slot shows an
    honest placeholder rather than a fabricated one."""
    title = html.escape(_archetype_title(archetype_key))
    initial = html.escape(title[:1].upper())
    avatar_class = "ads-avatar da" if archetype_key == DEVILS_ADVOCATE_KEY else "ads-avatar"
    return (
        f'<div class="ads-card">'
        f'<div class="{avatar_class}">{initial}</div>'
        f'<div class="ads-card-name ads-placeholder">Assigned at convene</div>'
        f'<div class="ads-card-title small-caps-label">{title}</div>'
        f"</div>"
    )


def _render_panel_preview_row(
    defense_type: DefenseType, other_subtype: OtherSubtype | None, panel_size: int, document_id: str
) -> None:
    """Live panel-composition preview (Task 1) — `compose_full_roster` is a pure,
    instant, zero-LLM function (Decision 1's own source), so it's safe to call on
    every rerun. `domain`/`topic` are irrelevant to roster composition (only
    `defense_type`/`other_subtype`/`panel_size` feed `compose_panel`'s lookup) —
    this profile is a disposable preview object, never stored, never the one that
    starts the session."""
    preview_profile = DefenseProfile(
        defense_type=defense_type,
        other_subtype=other_subtype,
        domain="preview",
        topic="preview",
        panel_size=panel_size,
        document_id=document_id,
    )
    archetype_keys = compose_full_roster(preview_profile)
    st.markdown('<p class="small-caps-label">Your panel — composed from the profile</p>', unsafe_allow_html=True)
    # Flex row instead of st.columns (bug fix): st.columns divides the row into
    # `len(archetype_keys)` equal-width slots with no floor, and nested one level
    # inside the bordered "Defense profile" container that width shrinks further
    # still — five columns there was narrow enough to force mid-word character
    # breaks ("Implementatio" / "n"). A flex-wrap row gives every card a real
    # min-width and wraps extra cards onto a second line instead of compressing.
    cards_html = "".join(_render_panel_preview_card(key) for key in archetype_keys)
    st.markdown(f'<div class="ads-preview-row">{cards_html}</div>', unsafe_allow_html=True)


def _turn_header(turn_num: int, panelist_name: str, archetype_key: str) -> str:
    """Permanent small-caps header (v0.3d Decision 8) — attribution lives in the
    transcript block itself, not a floating avatar, so it can't scroll away from
    its content."""
    return f"Turn {turn_num} — Dr. {panelist_name} · {_archetype_title(archetype_key)}"


def _render_turn_content(turn) -> None:
    """Question + grounding + (once answered) the candidate's inset answer — the
    body shared by a collapsed-history block and the active turn block."""
    st.write(turn.question)
    st.caption(f'Grounding: "{turn.grounding_reference}"')
    if turn.answer is not None:
        st.markdown(
            '<div class="ads-answer-inset">'
            '<div class="ads-answer-label">You —</div>'
            f"<div>{html.escape(turn.answer)}</div>"
            "</div>",
            unsafe_allow_html=True,
        )


def _render_exchange_history(session: DefenseSession) -> None:
    """Every turn in `session.turns` is, by construction, already scored (appended
    only after scoring completes) — so every history block collapses to its header
    line via a native `st.expander` (Decision 8 item 3), making a six-turn session
    read as a scannable docket rather than an ever-growing feed."""
    for i, turn in enumerate(session.turns, start=1):
        header = _turn_header(i, turn.panelist_name, turn.panelist_archetype_key)
        with st.expander(header, expanded=False):
            _render_turn_content(turn)


def _render_current_exchange(session: DefenseSession, active_panelist: Panelist, turn_num: int) -> None:
    """Runs in the main script body, NOT fragment-scoped — this block's abort path calls
    `st.rerun()` on its very first, unconditional (non-widget-triggered) execution within
    a fresh full-script pass, and empirically that combination leaves the *previous*
    stage's elements un-cleared (observed live: an aborted-state screen with the old
    profile-stage form still rendered underneath it). Only the strictly-interactive,
    no-abort-on-first-render portion below (`_render_answer_fragment`) is fragment-scoped."""
    if st.session_state.pending_turn is None:
        with _turn_lock:
            # Re-check after acquiring the lock: if a concurrent (overlapping) rerun got
            # here first and already finished, pending_turn is no longer None and this
            # rerun has nothing left to do.
            if st.session_state.pending_turn is None:
                with st.spinner(f"Dr. {active_panelist.panelist_name} is preparing a question..."):
                    try:
                        st.session_state.pending_turn = _call_with_timeout(
                            _generate_question,
                            _new_provider(),
                            session,
                            st.session_state.chunks,
                            st.session_state.embedding_model,
                            active_panelist,
                            st.session_state.other_subtype_line,
                            st.session_state.gemini_model,
                            label="question generation",
                        )
                    except LLMProviderError as exc:
                        _abort("question generation", exc)
                        st.rerun()

    turn = st.session_state.pending_turn
    if turn is None:
        return

    _render_answer_fragment(session, active_panelist, turn_num, turn)


@st.fragment
def _render_answer_fragment(session: DefenseSession, active_panelist: Panelist, turn_num: int, turn) -> None:
    """Fragment-scoped (v0.3d Task 4): the answer text_area/submit button are the only
    interactive widgets mid-turn, and Streamlit's own chat-input guidance recommends
    fragment-scoping exactly this shape — scoping the rerun here means the panel-card
    row and full exchange history above are not redrawn on every turn-local rerun.
    `st.rerun()` inside a fragment defaults to a full-app rerun (not fragment-scoped),
    so the "advance past this turn" transitions below are unaffected. Unlike the
    question-generation step above, this fragment's abort paths fire from within a
    widget-triggered (button-click) rerun, not the fragment's unconditional first
    render — the combination that produced the stale-element bug above.

    Rendered inside a real `st.container(border=True, key="active_turn_block")`
    (Decision 8) — the accent border in `_inject_theme_css`'s `.st-key-
    active_turn_block` rule then wraps the question AND the answer box in one DOM
    container, not just visually adjacent elements. The key is a constant, not
    per-turn: only one active-turn container exists in the render tree at a time."""
    with st.container(key="active_turn_block", border=True):
        header = _turn_header(turn_num, turn.panelist_name, turn.panelist_archetype_key)
        st.markdown(f'<div class="ads-turn-header">{html.escape(header)}</div>', unsafe_allow_html=True)
        st.write(turn.question)
        st.caption(f'Grounding: "{turn.grounding_reference}"')
        answer = st.text_area("Your answer", key=f"answer_{turn_num}")
        submitted = st.button("Submit answer", key=f"submit_{turn_num}")

    if submitted:
        if not answer.strip():
            st.warning("Answer cannot be blank — please respond.")
        else:
            turn.answer = answer.strip()
            with _turn_lock:
                # Re-check after acquiring the lock: if a concurrent (overlapping) rerun
                # already scored and consumed this pending_turn, there's nothing left to do.
                if st.session_state.pending_turn is not None:
                    with st.spinner("Scoring your answer..."):
                        try:
                            turn.score = _call_with_timeout(
                                _score_answer,
                                _new_provider(),
                                turn,
                                active_panelist,
                                session.profile.defense_type.value,
                                label="answer scoring",
                            )
                        except LLMProviderError as exc:
                            _abort("answer scoring", exc)
                            st.rerun()

                    session.turns.append(turn)
                    session.difficulty_current = _clamp_difficulty(
                        session.difficulty_current + turn.score.difficulty_delta
                    )
                    st.session_state.pending_turn = None

                    if len(session.turns) >= MAX_TURNS:
                        # Driver-level wire-up (v0.3 hardening, Task 1d): same call CLI's
                        # `main()` already makes at session-end. Zero changes to
                        # `report.py`/`engine.py` — orchestration only.
                        try:
                            with st.spinner("Building end-of-session report..."):
                                session.report = _call_with_timeout(
                                    build_report, session, _new_provider(), label="report narrative"
                                )
                        except LLMProviderError as exc:
                            _abort("report narrative", exc)
                            st.rerun()
                        else:
                            st.session_state.stage = "done"
                    else:
                        time.sleep(MODEL_CALL_DELAY_SECONDS.get(st.session_state.gemini_model, DEFAULT_CALL_DELAY))
            st.rerun()


def _render_report(report: DefenseReport) -> None:
    """Straight rendering of `DefenseReport` (v0.3d Decision 6) — nothing computed here
    beyond display formatting (1-indexed turn numbers, `:.2f` rounding); every number on
    screen traces back to a report field."""
    st.subheader("Session report")

    if report.narrative_fallback_used or not report.narrative:
        st.write("Narrative unavailable this session")
    else:
        st.write(report.narrative)

    trajectory = " → ".join(str(d) for d in report.difficulty_trajectory)
    st.markdown(f"**Difficulty trajectory:** {trajectory}")

    avg_cols = st.columns(3)
    avg_cols[0].metric("Avg clarity", f"{report.overall_avg_clarity:.2f}")
    avg_cols[1].metric("Avg depth", f"{report.overall_avg_depth:.2f}")
    avg_cols[2].metric("Avg grounding", f"{report.overall_avg_grounding:.2f}")

    st.markdown('<p class="small-caps-label">Per-panelist</p>', unsafe_allow_html=True)
    for section in report.panelist_sections:
        archetype_title = _archetype_title(section.archetype_key)
        st.markdown(f"**Dr. {section.panelist_name}** — {archetype_title} · {section.turns_taken} turn(s)")
        if section.turns_taken:
            st.caption(
                f"clarity {section.avg_clarity:.2f} · depth {section.avg_depth:.2f} · "
                f"grounding {section.avg_grounding:.2f}"
            )
            for gap in section.primary_gaps:
                st.write(f"- {gap}")

    st.markdown('<p class="small-caps-label">Pushback events</p>', unsafe_allow_html=True)
    if not report.pushback_events:
        st.write("No escalation moments this session")
    else:
        st.table(
            [
                {
                    "Turn": event.turn_index + 1,
                    "Difficulty": f"{event.difficulty_from} → {event.difficulty_to}",
                    "Outcome": event.outcome.value,
                }
                for event in report.pushback_events
            ]
        )


def _count_pdf_pages(path: str) -> int:
    """Page count for the case-file sidebar (v0.3d Decision 9). Reads the same temp
    file `chunk_pdf` already opened, rather than changing that function's contract
    (`list[str]` of chunk texts) — kept in the Streamlit layer per the brief's
    instruction not to touch ingestion modules for a display-only value. PyMuPDF is
    already a hard dependency via `chunking.py`'s own lazy import of it."""
    import fitz  # PyMuPDF

    document = fitz.open(path)
    try:
        return document.page_count
    finally:
        document.close()


def _ingest_and_extract(uploaded_file) -> None:
    """Ingestion + extraction only (Decision 4) — the defense profile form renders
    below the dropzone on the same 'intake' stage once `document_id` is set here
    (Sean's combined-page ask); this function never advances `stage` itself, so
    a rerun after it just re-renders 'intake' with the profile section now
    visible. `document_id`'s presence is what gates that, so this never re-fires
    for the same document."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name
        texts = chunk_pdf(tmp_path)
        page_count = _count_pdf_pages(tmp_path)
    except DocumentIngestionError as exc:
        st.error(f"Could not process document: {exc}")
        return
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)

    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]

    settings = load_settings()

    with st.spinner("Extracting domain/topic from the document..."):
        extraction = extract_document_profile(texts, _new_provider())

    st.session_state.document_id = str(uuid4())
    st.session_state.uploaded_filename = uploaded_file.name
    st.session_state.page_count = page_count
    st.session_state.chunks = chunks
    st.session_state.embedding_model = embedding_model
    st.session_state.gemini_model = settings.gemini_model
    st.session_state.extracted_domain = extraction.domain
    st.session_state.extracted_topic = extraction.topic


def _render_case_file_sidebar() -> None:
    """Case-file sidebar (v0.3d Decision 9): session context that otherwise has no
    home. Every value here is already computed elsewhere — zero new LLM calls,
    zero business-logic changes. Deliberately excludes the retrieved excerpt (would
    make the defense open-book) and anything difficulty- or score-shaped (same
    never-surfaced rule as dev-view's difficulty hiding, Decision 4)."""
    session: DefenseSession | None = st.session_state.get("session")
    if session is None:
        return

    with st.sidebar:
        st.markdown('<p class="sidebar-section-label">Case file</p>', unsafe_allow_html=True)
        st.caption(st.session_state.get("uploaded_filename", "—"))
        st.write(f"**Domain:** {session.profile.domain}")
        st.write(f"**Topic:** {session.profile.topic}")
        page_count = st.session_state.get("page_count", "?")
        chunk_count = len(st.session_state.get("chunks", []))
        st.caption(f"{page_count} pages · {chunk_count} chunks embedded")

        st.markdown('<p class="sidebar-section-label">Panel</p>', unsafe_allow_html=True)
        if session.report is not None:
            # Session complete — nobody is "speaking" anymore; every card is just
            # its final count, same wording _panelist_card_state uses.
            for panelist in session.panel:
                turns_taken = sum(1 for t in session.turns if t.panelist_archetype_key == panelist.archetype_key)
                noun = "question" if turns_taken == 1 else "questions"
                st.caption(f"Dr. {panelist.panelist_name} — {turns_taken} {noun} asked")
        else:
            turn_num = len(session.turns) + 1
            active_panelist = select_active_panelist(session, turn_num)
            for panelist in session.panel:
                _, status_line = _panelist_card_state(panelist, session, active_panelist)
                st.caption(f"Dr. {panelist.panelist_name} — {status_line}")

        st.markdown('<p class="sidebar-section-label">Session</p>', unsafe_allow_html=True)
        turn_progress = min(len(session.turns) + 1, MAX_TURNS)
        st.caption(f"Turn {turn_progress} of {MAX_TURNS} · {session.profile.defense_type.value}")


_render_dev_view()
_render_case_file_sidebar()

if st.session_state.stage == "intake":
    # One combined page (Sean's ask): the dropzone and the defense-profile form
    # live on the same screen, matching the design prototype's composition. The
    # profile section only appears once `document_id` exists — that's the real
    # signal ingestion + extraction finished, not a separate navigated-to stage.
    document_ready = "document_id" in st.session_state

    if not document_ready:
        st.markdown(
            '<p style="text-align:center;font-weight:600;margin-bottom:0.25rem;">'
            "Drop your research document</p>",
            unsafe_allow_html=True,
        )
        uploaded_file = st.file_uploader(
            "Upload your research document (PDF)", type=["pdf"], label_visibility="collapsed"
        )
        st.caption("PDF · thesis, capstone, or paper")

        if st.button("Process document", type="primary", disabled=uploaded_file is None):
            with st.spinner("Processing document..."):
                _ingest_and_extract(uploaded_file)
            if "document_id" in st.session_state:
                st.rerun()
    else:
        # Loaded-document summary (replaces the empty dropzone prompt) — same
        # composition as the prototype's docLoaded branch: filename, ingestion
        # stats, a "read by panel" confirmation.
        st.markdown(
            '<div class="ads-card" style="text-align:left;display:flex;align-items:center;gap:14px;">'
            '<div class="ads-avatar" style="border-radius:4px;font-size:0.7rem;">PDF</div>'
            '<div style="flex:1;min-width:0;">'
            f'<div class="ads-card-name">{html.escape(st.session_state.uploaded_filename)}</div>'
            '<div class="ads-card-status" style="margin-top:2px;">'
            f"ingested · {st.session_state.page_count} pages · {len(st.session_state.chunks)} chunks embedded"
            "</div></div>"
            '<div class="ads-speaking-badge">✓ Read by panel</div>'
            "</div>",
            unsafe_allow_html=True,
        )

    if document_ready:
        with st.container(border=True):
            st.markdown('<p class="small-caps-label">Defense profile</p>', unsafe_allow_html=True)

            defense_type = st.pills(
                "Defense type",
                list(DefenseType),
                format_func=lambda dt: dt.value,
                default=DefenseType.THESIS,
                required=True,
                key="defense_type_pill",
            )
            other_subtype = None
            if defense_type == DefenseType.OTHER:
                other_subtype = st.selectbox("Subtype", list(OtherSubtype), format_func=lambda ost: ost.value)

            domain_col, topic_col = st.columns(2)
            with domain_col:
                domain = st.text_input("Domain / discipline", value=st.session_state.extracted_domain)
            with topic_col:
                topic = st.text_input("Research title / topic", value=st.session_state.extracted_topic)

            max_panel_size = len(PANEL_COMPOSITION[_composition_key(defense_type, other_subtype)])
            panel_size = st.number_input(
                "Panel size", min_value=1, max_value=max_panel_size, value=max_panel_size
            )
            # Panel caption (v0.3d Decision 1) — compose_full_roster appends Devil's Advocate
            # outside panel_size unconditionally, so the visible panel is always one bigger than
            # the selector value; state the arithmetic explicitly rather than let it read as a bug.
            st.markdown(
                f'<p class="small-caps-label">{int(panel_size)} domain panelists + '
                f"Devil's Advocate = {int(panel_size) + 1} total</p>",
                unsafe_allow_html=True,
            )

            _render_panel_preview_row(defense_type, other_subtype, int(panel_size), st.session_state.document_id)

        _, cta_col, _ = st.columns([1, 1, 1])
        with cta_col:
            start_clicked = st.button("Convene the Panel", type="primary", use_container_width=True)

        if start_clicked:
            if not domain.strip() or not topic.strip():
                st.error("Domain and topic are required.")
            else:
                profile = DefenseProfile(
                    defense_type=defense_type,
                    other_subtype=other_subtype,
                    domain=domain.strip(),
                    topic=topic.strip(),
                    panel_size=int(panel_size),
                    document_id=st.session_state.document_id,
                )
                with st.spinner("Assembling the panel..."):
                    archetype_roster = compose_full_roster(profile)
                    panel, fallback_used = generate_panel(profile, archetype_roster, _new_provider())

                st.session_state.session = DefenseSession(
                    profile=profile, panel=panel, difficulty_current=profile.difficulty_start
                )
                st.session_state.personas_fallback_used = fallback_used
                st.session_state.other_subtype_line = (
                    f"\n- Defense subtype: {other_subtype.value}" if other_subtype is not None else ""
                )
                st.session_state.pending_turn = None
                st.session_state.stage = "running"
                st.rerun()

elif st.session_state.stage == "running":
    session: DefenseSession = st.session_state.session
    turn_num = len(session.turns) + 1
    active_panelist = select_active_panelist(session, turn_num)

    # Difficulty is deliberately absent here (v0.3d Decision 4, item 3) — it never
    # renders in the main flow mid-session, only in the report's trajectory after the
    # session ends. It remains visible in dev-view (`_render_dev_view` above).
    st.subheader(f"Turn {turn_num}/{MAX_TURNS}")

    _render_panel_row(session, active_panelist)
    _render_exchange_history(session)
    _render_current_exchange(session, active_panelist, turn_num)

elif st.session_state.stage == "aborted":
    st.error(ABORT_MESSAGES[st.session_state.abort_stage])
    if st.button("Start a new session", key="reset_from_aborted"):
        _reset()
        st.rerun()

elif st.session_state.stage == "done":
    session = st.session_state.session
    st.success(f"Session complete — {len(session.turns)} turns.")

    if session.report is not None:
        _render_report(session.report)

    # PROMPT_VERSION and personas_fallback_used are stamped only here, at export time —
    # DefenseSession stays free of any coupling to the prompts module or generation
    # provenance. This JSON is the seed of v1.0 analytics and the artifact format for
    # future eval runs.
    export_payload = {
        "prompt_version": PROMPT_VERSION,
        "personas_fallback_used": st.session_state.personas_fallback_used,
        "session": json.loads(session.model_dump_json()),
    }
    st.download_button(
        "Download transcript",
        data=json.dumps(export_payload, indent=2),
        file_name=f"defense_session_{session.profile.document_id}.json",
        mime="application/json",
    )

    if st.button("Start a new session", key="reset_from_done"):
        _reset()
        st.rerun()
