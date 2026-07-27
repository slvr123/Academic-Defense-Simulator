"""Minimal Streamlit wrap — thin I/O layer over `engine.py`'s turn loop (v0.3b Task 6,
v0.3d defense-simulation UI). Reuses `engine.py`'s turn-loop helpers and
`DefenseSession` as-is; no changes to `retrieve()`, prompt templates, scoring, or
report logic. Flow (0.3a Decision 4): upload -> ingest -> extract (one LLM call,
cached per document_id) -> profile form (prefilled, editable) -> start session.
Panel composition is a multiselect scoped to the selected defense type/subtype, capped
at 3 domain archetypes (v0.3e Decision 6). v0.3j adds per-slot name/icon customization
rows and a visible `difficulty_start` select_slider (Decisions 1-4) — presentation and
profile plumbing only, zero prompt-template changes.

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

import base64
import concurrent.futures
import html
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd
import psutil
import streamlit as st
from pydantic import ValidationError

from academic_defense_simulator import analytics, persistence
from academic_defense_simulator.config import load_settings
from academic_defense_simulator.demo_counter import (
    consume_demo_session,
    demo_available,
    demo_daily_session_cap,
    demo_max_turns,
    demo_sessions_per_browser,
    demo_sessions_used_today,
)
from academic_defense_simulator.document_profile import extract_document_profile
from academic_defense_simulator.document_relevance import assess_document
from academic_defense_simulator.engine import (
    DEFAULT_CALL_DELAY,
    MODEL_CALL_DELAY_SECONDS,
    _clamp_difficulty,
    _generate_question,
    _score_answer,
    select_active_panelist,
    session_is_complete,
)
from academic_defense_simulator.example_session import (
    ExampleSessionUnavailable,
    load_example_session,
)
from academic_defense_simulator.llm.gemini_provider import GeminiProvider, validate_gemini_key
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError
from academic_defense_simulator.models.defense_profile import (
    DefenseProfile,
    DefenseType,
    OtherSubtype,
    PanelistCustomization,
)
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import DefenseReport
from academic_defense_simulator.models.session import DefenseSession, PersistedSession, SessionStage
from academic_defense_simulator.panel import (
    DEVILS_ADVOCATE_KEY,
    PANEL_COMPOSITION,
    archetype_default_icon,
    compose_full_roster,
    generate_panel,
    image_icon_path,
    list_icon_choices,
)
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PROMPT_VERSION
from academic_defense_simulator.rag.chunking import DocumentIngestionError, chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk
from academic_defense_simulator.report import build_report
from academic_defense_simulator.sample_document import SAMPLE_DISPLAY_NAME, load_sample_document

# Thin permanent call-lifecycle logging (v0.3 hardening, Task 1b) — light enough to ship,
# enough that a future hang recurrence has something to look at.


@st.cache_resource(show_spinner=False)
def _configure_logging_once() -> None:
    """v1.0a live-pass fix: replaces the original `if not logging.getLogger().handlers:
    logging.basicConfig(...)` guard. That guard silently no-ops whenever *anything* has
    already attached a handler to root by the time this module runs, regardless of what
    level or destination that handler uses — the leading suspect for why the cache-miss/RSS
    INFO lines never showed up in Cloud logs despite the code paths demonstrably running.
    Confirmed locally (via the real `streamlit run` CLI entrypoint, not a bare `import`):
    root has zero handlers before this module runs, so the original guard fires correctly
    here — meaning this is specifically a Cloud-environment difference, not reproducible
    locally, and this fix is defense against that difference rather than a confirmed
    root-cause patch. `force=True` tears down whatever's already on root and applies ours
    unconditionally, so every module's logger (this one, gemini_provider, engine, ...) gets
    a working INFO-level handler regardless of what ran first. Wrapped in `st.cache_resource`
    — the same run-once-per-process primitive `_load_embedding_model` already relies on — so
    the teardown-and-rebuild happens once per process, not on every rerun of this module's
    top-level code."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", force=True)


_configure_logging_once()
logger = logging.getLogger(__name__)


def _log_rss(point: str) -> None:
    """v1.0a item 3: RSS at a named point, read out of Cloud logs — the free tier
    exposes no per-process memory dashboard, so this is the only way to see actual
    headroom under `torch`/sentence-transformers. Kept in this module (not a
    business-logic one) per the standing streamlit-import boundary."""
    rss = psutil.Process().memory_info().rss
    logger.info("RSS at %s: %d bytes (%.1f MB)", point, rss, rss / 1_000_000)


@st.cache_resource(show_spinner=False)
def _log_post_import_rss_once() -> None:
    """v1.0a live-pass fix: the direct `_log_rss(...)` call this replaces sat at
    module level, which Streamlit re-executes on every rerun (every widget
    interaction), not once per process — confirmed live, 5 identical
    "post-import" lines for a single session. `st.cache_resource` is the same
    process-wide, run-once-across-reruns primitive `_load_embedding_model`
    already relies on (real evidence: object identity held across 3 reruns),
    reused here to give this checkpoint the once-per-process semantics item 3
    actually wants."""
    _log_rss("post-import, before any model load")


_log_post_import_rss_once()

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


# v0.3h Brief: one label vocabulary for every LLM-touching stage, shared by
# ABORT_MESSAGES below, `_call_with_timeout`'s `label=`, and CallCounter's
# `by_stage` breakdown — a single set of strings rather than three that have to be
# kept in sync by hand. The gate/extraction/persona stages had no label anywhere
# before this brief (they don't go through `_call_with_timeout`); adding one for
# each was in scope per the brief's "if the gate/extraction/persona calls lack
# labels, add them" instruction.
LLM_STAGE_RELEVANCE_GATE = "relevance gate"
LLM_STAGE_EXTRACTION = "domain/topic extraction"
LLM_STAGE_PERSONA_GENERATION = "persona generation"
LLM_STAGE_QUESTION_GENERATION = "question generation"
LLM_STAGE_ANSWER_SCORING = "answer scoring"
LLM_STAGE_REPORT_NARRATIVE = "report narrative"
LLM_STAGE_ANSWER_SUGGESTIONS = "answer suggestions"  # v1.0b-2 — independent of the narrative call
LLM_STAGE_GAP_CLUSTERING = "gap theme clustering"  # v1.0b analytics — cross-session, not per-session
# v1.0b-2: build_report now makes two independent calls (narrative + suggestions).
# LLM_STAGE_REPORT_BUILD labels the single outer _call_with_timeout wrapping both —
# LLM_STAGE_REPORT_NARRATIVE/LLM_STAGE_ANSWER_SUGGESTIONS remain the two providers'
# own labels, so CallCounter.by_stage still attributes each network attempt correctly.
LLM_STAGE_REPORT_BUILD = "end-of-session report"

# v0.4a Decision 6 — the demo turn cap routes through this same stage-keyed abort
# copy dict, not a new state-machine state; it's simply a fourth key alongside the
# three LLM-stage ones below, exactly like adding a fourth label was already the
# extension point (see the comment above ABORT_MESSAGES).
DEMO_TURN_CAP_ABORT_STAGE = "demo_turn_cap"

# Stage-keyed abort copy (v0.3d Decision 5) — the user never sees raw exception text;
# it goes to logs (via `_abort` below) and the dev-view sidebar only. Keys are the exact
# `label` values already passed to `_call_with_timeout` at each of the three call sites
# — they were already mutually distinguishing, so no separate stage-key mechanism was
# needed (brief's "reuse the existing label if it's already distinguishing" note).
ABORT_MESSAGES = {
    LLM_STAGE_QUESTION_GENERATION: "The panel's next question took longer than expected.",
    LLM_STAGE_ANSWER_SCORING: "Scoring your answer took longer than expected.",
    LLM_STAGE_REPORT_BUILD: "Building your report took longer than expected.",
    DEMO_TURN_CAP_ABORT_STAGE: (
        "Demo limit reached — the panel adjourns early. Bring a free Gemini key "
        "(60 seconds, same screen as before you started) to sit a full defense."
    ),
}


def _persistence_enabled() -> bool:
    """v0.4b Decision 1 — the standing gate every persistence call site checks
    first. Off means this module never touches `academic_defense_simulator.persistence`
    at all: no directory, no save calls, no resume UI (Decision 1's "off-mode is
    behavioral parity with today" property)."""
    return load_settings().persistence_enabled


def _build_persisted_session(stage: SessionStage) -> PersistedSession:
    """Assembles the save unit from current `st.session_state` (v0.4b Decision 2).
    `document_chunks` is re-derived from `st.session_state.chunks` on every save
    rather than cached separately — one source of truth, no drift risk."""
    return PersistedSession(
        session_id=st.session_state.session_id,
        created_at=st.session_state.session_created_at,
        updated_at=datetime.now(timezone.utc),
        stage=stage,
        session=st.session_state.session,
        document_chunks=[c.text for c in st.session_state.chunks],
    )


def _is_stale_class_error(exc: ValidationError) -> bool:
    """True only for the exact confirmed shape of the dev-hot-reload
    identity-mismatch fault: every error in the exception is a `model_type`
    failure on the `session` field, nothing else. Deliberately narrow — a
    `ValidationError` with any other error mixed in (a different field, a
    different error type) is a real, unexpected data problem and must not be
    swallowed under this label."""
    errors = exc.errors()
    return bool(errors) and all(e.get("type") == "model_type" and e.get("loc") == ("session",) for e in errors)


def _persist(stage: SessionStage) -> None:
    """Save-call wiring (v0.4b Decision 3): called at turn completion, session
    completion, and abort — never mid-turn. A no-op when persistence is off, or
    when this browser session never minted a `session_id` (persistence turned on
    but no session has started yet — e.g. still on the intake screen). Disk failure
    is logged and surfaced as a non-blocking caption (`save_failed`); the live
    session is never interrupted by it, and a later successful save clears the
    caption again.

    v0.4b amendment (dev-hot-reload session-identity mismatch): a second,
    distinct except branch for `pydantic.ValidationError` — deliberately not
    merged into the OSError branch above. Disk trouble and a stale class
    reference from Streamlit's dev-mode module reload are different failure
    classes with different causes and different remedies (retry vs. restart the
    app); keeping their log lines and captions distinct is what makes either one
    traceable from the logs alone. Confirmed unreachable in a real deploy —
    Streamlit Cloud restarts the whole process on every deploy rather than
    reloading modules in a live one, which wipes `st.session_state` instead of
    leaving it holding a stale class reference. Local-dev-only, same as the
    circumstance that produces it.

    Narrowed per review: only a `ValidationError` whose *every* error entry is
    the specific `model_type` failure on the `session` field is treated as the
    stale-class case — that's the exact, confirmed shape the dev-hot-reload
    fault produces (see `_is_stale_class_error` below). Any other
    `ValidationError` (a genuinely malformed payload reaching this constructor)
    re-raises instead of being silently absorbed here — `_build_persisted_session`
    only feeds this call already-validated internal state today, so that path
    isn't expected to fire, but a future change that feeds it less-trusted data
    must not have a real data bug mislabeled as harmless reload noise."""
    if not _persistence_enabled() or "session_id" not in st.session_state:
        return
    try:
        persistence.save_session(_build_persisted_session(stage))
    except OSError as exc:
        logger.warning("session save failed (session_id=%s): %s", st.session_state.session_id, exc)
        st.session_state.save_failed = True
        st.session_state.save_failed_reason = "disk"
    except ValidationError as exc:
        if not _is_stale_class_error(exc):
            raise
        logger.warning(
            "Save skipped: stale class reference detected (likely mid-session code reload). %s", exc
        )
        st.session_state.save_failed = True
        st.session_state.save_failed_reason = "stale_class"
    else:
        st.session_state.save_failed = False
        st.session_state.save_failed_reason = None


def _abort(label: str, exc: LLMProviderError) -> None:
    logger.error("session aborted at stage %r: %s", label, exc)
    st.session_state.stage = "aborted"
    st.session_state.abort_stage = label
    st.session_state.abort_message = str(exc)
    _persist(SessionStage.ABORTED)


def _abort_demo_limit() -> None:
    """v0.4a Decision 6, path 1: demo turn cap reached mid-session. Reuses the same
    'aborted' stage as a real provider failure — no new state — but this isn't a
    failure, so it logs at INFO (not ERROR via `_abort`) and sets no
    `abort_message`: there's no exception here, so dev-view's raw-exception
    caption correctly has nothing to show."""
    logger.info("demo session ended: turn cap reached")
    st.session_state.stage = "aborted"
    st.session_state.abort_stage = DEMO_TURN_CAP_ABORT_STAGE
    _persist(SessionStage.ABORTED)


# Stages where the sidebar carries live session context worth having open on arrival:
# the case file, the panel roster, and turn progress all exist only once a defense is
# under way. Intake and analytics get nothing from it, so it starts out of the way.
_SIDEBAR_EXPANDED_STAGES = frozenset({"running", "aborted", "done"})

# Verified against Streamlit 1.58 (headless browser, this repo's app): `initial_sidebar_state`
# is applied on every script run, not just the first, but only when the value CHANGES.
# Re-sending the same value is a no-op, and any manual toggle by the user disables the
# mechanism outright for the rest of that page load. That gives exactly the behavior we
# want from one stage-driven expression: collapsed through intake, then the flip to
# "expanded" when the conversation with the panel starts opens it; every later turn
# re-sends "expanded" unchanged, so a mid-session collapse by the user sticks. It is
# also why intake declares "collapsed" rather than the old unconditional "expanded":
# without a value change there is no way to open the sidebar at the stage transition.
st.set_page_config(
    page_title="Academic Defense Simulator",
    initial_sidebar_state=(
        "expanded" if st.session_state.get("stage") in _SIDEBAR_EXPANDED_STAGES else "collapsed"
    ),
)


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
        /* Uploaded icon images (v0.3j amendment): fill the avatar circle, crop to
           cover — the striped/solid background only shows for emoji avatars. */
        .ads-avatar img {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
        }
        /* Icon-picker popover (v0.3j amendment 2): the grid needs real width —
           Streamlit sizes popovers to content, which collapses the image grid
           into a sliver without a floor. Kept compact (Sean: first cut was too
           big): ~64px cells, small select-button type. */
        div[data-testid="stPopoverBody"] {
            min-width: 330px;
            max-width: 330px;
        }
        div[data-testid="stPopoverBody"] img {
            border-radius: 50%;
        }
        div[data-testid="stPopoverBody"] button p {
            font-size: 0.68rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        div[data-testid="stPopoverBody"] button {
            min-height: 1.6rem;
            padding: 0 0.25rem;
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

        /* Example-stage banner (v1.0.1 Decision 5, copy per Appendix B3). Same
           keyed-container technique as .st-key-active_turn_block. This started
           as st.info() and had to change: the native info box renders blue,
           which is the one colour nowhere else in this palette, and it is the
           first thing a visitor sees on the screen the demo-exhausted gate
           sends them to. The oxblood left rule matches .ads-answer-inset's
           shape language — a quiet aside, not an alert. */
        .st-key-example_banner {
            background: #1A1714;
            border-left: 3px solid #8C3A3F !important;
            border-radius: 0 8px 8px 0;
            padding: 0.9rem 1.1rem;
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

        /* Sidebar-as-overlay (UI fix): the sidebar was pushing main content, which
           re-centers .stMainBlockContainer against shrinking/growing leftover space
           rather than the true viewport — main content visibly shifted left/right on
           toggle. Fixed-position overlay + a main-container max-width anchored to the
           viewport fixes the shift; accepted trade-off is that the open sidebar covers
           left-edge main content instead of squeezing it aside, which is fine for a
           reference panel (case file), not primary reading content. */
        [data-testid="stSidebar"] {
            position: fixed !important;
            height: 100vh !important;
            z-index: 999991;
        }
        [data-testid="stSidebar"] > div:first-child {
            box-shadow: 2px 0 16px rgba(0, 0, 0, 0.4);
        }
        /* Bug fix (found live while testing v0.3g's intake flow): plain `auto` centers
           against the FULL viewport, same as the fixed-position sidebar no longer
           participating in layout — on any viewport under ~2100px wide (i.e. virtually
           all real usage) that centered box's left edge lands underneath the sidebar's
           300px width (measured live), not beside it. On the intake screen this wasn't
           cosmetic: the "Process document" and "Proceed anyway" buttons were physically
           unclickable without the sidebar collapsed first. `max(300px, ...)` clamps the
           left offset to always clear the sidebar while leaving the wide-viewport
           behavior (where natural centering already exceeds 300px) unchanged — still no
           shift on toggle, since the sidebar stays out of flow either way. */
        .stMainBlockContainer {
            max-width: 900px;
            margin-left: max(300px, calc(50vw - 450px)) !important;
            margin-right: auto;
        }

        /* Resume-session list (v0.4b-surface): compact single row per session —
           the old st.container(border=True) card rendered at full card height
           with two 50/50 buttons, giving Delete equal visual weight to Resume.
           Padding is trimmed to ~half that height and Delete is demoted to a
           ghost button below. */
        div[class*="st-key-session_row_"] {
            padding: 0.6rem 0.9rem !important;
        }
        .ads-session-title {
            margin: 0;
            font-size: 0.94rem;
            font-weight: 400;
            color: #ECE7DD;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .ads-session-meta {
            margin: 0.1rem 0 0 0;
            font-size: 0.75rem;
            color: #8A8378;
        }
        /* Delete stays a ghost button (transparent, thin muted border) so it
           doesn't compete with Resume's oxblood fill for attention. */
        div[class*="st-key-session_delete_"] button {
            background-color: transparent !important;
            border: 1px solid #4A443C !important;
            color: #B5AEA2 !important;
        }
        div[class*="st-key-session_delete_"] button:hover {
            border-color: #8C3A3F !important;
            color: #ECE7DD !important;
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
        "Upload your research. Face a panel that has actually read it — and defend "
        "your study against questions that get harder every turn.</p>",
        unsafe_allow_html=True,
    )


# v1.0.1 Decision 7 — locked positioning copy. Held as constants for the same
# reason the example banner is: the decisions doc is the source of truth for this
# wording, and a test asserts these render verbatim so drift shows up as a failure
# rather than as a quietly reworded first screen.
POSITIONING_HEADLINE = "Most RAG demos answer questions about your document. This one asks them."

POSITIONING_EXPANDER_TITLE = "How this works"

# Bullet one is the *verified* variant, not Decision 7's original string. The
# original claimed "embedded locally — nothing about it is stored on a server",
# which Decision 7 itself required be verified true on the deployed path before
# shipping. Verification (this session): persistence is off on deploy —
# `ADS_PERSISTENCE_ENABLED` unset resolves False, so no session JSON carrying
# `document_chunks` is ever written there — but retrieved passages *are* sent to
# Gemini on every question-generation call (`engine.py`, `retrieved_chunk=chunk_text`
# interpolated into the panelist prompt). The original bullet implied the document
# never leaves the machine, which is false. Sean locked this replacement in advance
# for exactly this finding.
POSITIONING_BULLETS = (
    "Your document is processed in memory and never stored. Retrieved passages "
    "are sent to the Gemini API to generate each question.",
    "Each panelist retrieves a passage and writes a question grounded in that "
    "specific passage, so the questions are about *your* work, not the topic in "
    "general.",
    "Your answer is scored behind the scenes, and that score steers how hard the "
    "next question is. You never see the score during the session.",
)


def _render_positioning_copy() -> None:
    """Decision 7: one sentence and three bullets, above the mode radio.

    The headline carries the whole differentiator — RAG generates the questions,
    not the answers — and the expander is collapsed by default so the fold stays
    clear on a phone for anyone who does not want the mechanism explained."""
    st.markdown(
        '<p style="text-align:center;font-size:1.05rem;line-height:1.5;'
        'max-width:620px;margin:0 auto 0.75rem;">'
        f"{html.escape(POSITIONING_HEADLINE)}</p>",
        unsafe_allow_html=True,
    )
    with st.expander(POSITIONING_EXPANDER_TITLE):
        for bullet in POSITIONING_BULLETS:
            st.markdown(f"- {bullet}")


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
    # v0.4a Decision 5: "N demo sessions per browser session" has to survive this
    # reset — the aborted/done screens both offer "Start a new session", and that
    # button is one click away from the demo-turn-cap abort screen specifically.
    # Without preserving the count here, that click would silently hand out a fresh
    # demo, defeating the cap it just enforced.
    demo_sessions_started = st.session_state.get("demo_sessions_started", 0)
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.stage = "intake"
    st.session_state.demo_sessions_started = demo_sessions_started


def _composition_key(defense_type: DefenseType, other_subtype: OtherSubtype | None) -> str:
    if defense_type == DefenseType.OTHER:
        assert other_subtype is not None
        return f"other/{other_subtype.value}"
    return defense_type.value


def _active_gemini_key() -> str:
    """v0.4a Decision 4: this session's Gemini key — the user's own validated key
    in own-key mode, the project key (secrets/env, exactly as pre-v0.4a) in demo
    mode. Resolved once at the intake gate (`_render_key_gate`) and read from here
    by every provider construction, so a Streamlit rerun on a fresh thread never
    re-derives, caches, or shares a client across different users' keys — the
    cross-user leak `st.cache_resource` would have risked (Decision 4)."""
    return st.session_state.active_api_key


def _new_provider(label: str) -> GeminiProvider:
    """A fresh GeminiProvider (and its underlying HTTP client) per call, rather than one
    reused across reruns via st.session_state. Streamlit's script-runner executes each
    rerun on a new thread, and reusing a single genai.Client's connection pool across
    threads was observed to hang indefinitely on a later call in the same session.

    v0.3h Brief: `label` and the session's `CallCounter` (if one exists yet) are
    threaded into every construction — this is the one place that decides which
    stage a given provider instance's calls get attributed to. v0.4a: the API key
    itself is now session-resolved (`_active_gemini_key`), not always the project
    key — only `gemini_model` still comes from `load_settings()` unconditionally,
    since model selection is out of this slice's scope either way."""
    settings = load_settings()
    return GeminiProvider(
        api_key=_active_gemini_key(),
        model=settings.gemini_model,
        call_counter=st.session_state.get("llm_call_counter"),
        label=label,
    )


# v0.3g Brief: the relevance gate is a judgment task, pinned to gemini-2.5-flash
# regardless of GEMINI_MODEL — flash-lite is already on record as unfit for judgment
# calls (inverted difficulty_delta, inflated clarity scores). One call per upload, so
# the RPD cost is negligible even off the dev-default model.
_RELEVANCE_ASSESSMENT_MODEL = "gemini-2.5-flash"


def _new_relevance_provider() -> GeminiProvider:
    return GeminiProvider(
        api_key=_active_gemini_key(),
        model=_RELEVANCE_ASSESSMENT_MODEL,
        call_counter=st.session_state.get("llm_call_counter"),
        label=LLM_STAGE_RELEVANCE_GATE,
    )


# v1.0b-2 Decision 2: answer suggestions is a judgment task (what a stronger answer
# would specifically have included), pinned to gemini-2.5-flash regardless of
# GEMINI_MODEL — same rationale, same pattern as `_new_relevance_provider`.
_ANSWER_SUGGESTIONS_MODEL = "gemini-2.5-flash"


def _new_suggestions_provider() -> GeminiProvider:
    return GeminiProvider(
        api_key=_active_gemini_key(),
        model=_ANSWER_SUGGESTIONS_MODEL,
        call_counter=st.session_state.get("llm_call_counter"),
        label=LLM_STAGE_ANSWER_SUGGESTIONS,
    )


# v1.0b Decision 2: gap-theme clustering is a judgment task (grouping free text by
# meaning), pinned to gemini-2.5-flash regardless of GEMINI_MODEL — same rationale,
# same pattern as `_new_relevance_provider` above.
_ANALYTICS_MODEL = "gemini-2.5-flash"


def _new_analytics_provider() -> GeminiProvider:
    return GeminiProvider(
        api_key=_active_gemini_key(),
        model=_ANALYTICS_MODEL,
        call_counter=st.session_state.get("llm_call_counter"),
        label=LLM_STAGE_GAP_CLUSTERING,
    )


@st.cache_data(show_spinner=False)
def _cached_cluster_gap_themes(cache_key: tuple, _provider: GeminiProvider):
    """v1.0b Decision 3: cached on `(sorted primary_gap strings,
    ANALYTICS_PROMPT_VERSION)` — new completed session or a prompt-version bump both
    produce a new key, everything else is a cache hit. `_provider` is prefixed with
    an underscore so Streamlit excludes it from hashing (a GeminiProvider/genai.Client
    isn't a meaningful or stable cache key component); `cache_key` alone determines
    hits/misses, exactly per Decision 3. Thin pass-through to
    `analytics.cluster_gap_themes` — the actual clustering logic and fail-open
    behavior live there, not here."""
    primary_gaps = list(cache_key[0])
    return analytics.cluster_gap_themes(primary_gaps, _provider)


def _render_key_gate() -> bool:
    """v0.4a Decision 1 — mode radio above the upload step. Returns True once
    `st.session_state.active_api_key` is resolved (own key validated, or a demo
    session granted); the caller renders nothing else and stops the script for
    this run while this returns False, so the dropzone/profile form below is
    unreachable until a key is ready — the pre-session half of Decision 6's two
    exhaustion paths (daily-cap-reached, demo-already-used) is enforced entirely
    here, before any upload or LLM call can happen."""
    if "active_api_key" in st.session_state:
        return True

    with st.container(border=True):
        st.markdown('<p class="small-caps-label">Choose how to run this session</p>', unsafe_allow_html=True)

        demo_sessions_started = st.session_state.get("demo_sessions_started", 0)
        browser_limit_hit = demo_sessions_started >= demo_sessions_per_browser()
        daily_cap_hit = not demo_available()
        demo_disabled_reason = None
        if browser_limit_hit:
            demo_disabled_reason = "Already used this browser session — refresh to try again later."
        elif daily_cap_hit:
            used = demo_sessions_used_today()
            demo_disabled_reason = f"Today's {used}/{demo_daily_session_cap()} demo sessions are used — come back tomorrow, or use your own key."

        mode = st.radio(
            "Mode",
            options=["demo", "own_key"],
            format_func=lambda m: (
                "Use my own Gemini API key (recommended)"
                if m == "own_key"
                else "Demo mode — limited session" + (f" ({demo_disabled_reason})" if demo_disabled_reason else "")
            ),
            index=0,
            label_visibility="collapsed",
            key="api_key_mode_radio",
        )

        if mode == "own_key":
            with st.expander("Don't have a key? It's free — 60 seconds"):
                st.markdown(
                    "1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)\n"
                    "2. Sign in with any Google account\n"
                    "3. Click **Create API key**\n"
                    "4. Paste it below\n\n"
                    "Free tier — no billing setup, no card required."
                )
            key_input = st.text_input(
                "Gemini API key", type="password", key="own_key_input", label_visibility="collapsed",
                placeholder="Paste your Gemini API key",
            )
            validate_clicked = st.button("Use this key", type="primary")
            if validate_clicked:
                if not key_input.strip():
                    st.error("Paste a key first.")
                else:
                    with st.spinner("Checking your key..."):
                        ok = validate_gemini_key(key_input.strip())
                    if ok:
                        st.session_state.active_api_key = key_input.strip()
                        st.session_state.api_key_mode = "own"
                        st.rerun()
                    else:
                        st.error(
                            "This key was rejected by Google — check for missing characters "
                            "or create a fresh key."
                        )
            return False

        # demo mode
        st.caption(
            f"Demo sessions are capped at {demo_max_turns()} turns, "
            f"{demo_sessions_per_browser()} per browser session, "
            f"{demo_daily_session_cap()} total worldwide per day."
        )
        if demo_disabled_reason:
            st.error(demo_disabled_reason)
            # v1.0.1 Decision 5 — the launch-day failure this exists to prevent:
            # the post lands, the daily cap is reached, and every visitor after
            # that meets a disabled button and nothing else. Both exhaustion
            # paths (daily cap, already-used-this-browser) funnel through
            # `demo_disabled_reason`, so one control here covers both. This is
            # the one place the example view is offered at full prominence,
            # because in this state it is the only thing left to offer.
            st.write(
                "You can still watch a complete recorded defense — no key, no upload, "
                "nothing to set up."
            )
            if st.button(
                "See a recorded example session", key="goto_example_gate", type="primary"
            ):
                st.session_state.stage = "example"
                st.rerun()
            return False
        if st.button("Start demo session", type="primary"):
            # v0.4a Decision 5: the daily slot is spent here, at commit, not at
            # Convene — simpler than tracking whether an in-progress demo pick
            # ever turns into a real session, and Decision 5 already accepts
            # coarser gaming/concurrency tradeoffs at this scale.
            consume_demo_session()
            st.session_state.demo_sessions_started = demo_sessions_started + 1
            st.session_state.active_api_key = load_settings().gemini_api_key
            st.session_state.api_key_mode = "demo"
            st.rerun()
        return False


@st.cache_resource(show_spinner=False)
def _load_embedding_model() -> EmbeddingModel:
    """Cached across reruns and sessions within a server process (v0.3 deployment
    Decision 3) — sentence-transformers model loading is the real cold-start cost on
    Cloud, and without this every new visitor's session pays it again from scratch.
    `show_spinner=False` (post-0.3 deploy bugfix pass): st.cache_resource's default
    spinner reads "Running _load_embedding_model()." — a raw function name leaking
    into the UI — and this call already runs nested inside the caller's own
    "Processing document..." spinner, so a second, uglier one is redundant."""
    logger.info("embedding model load — cache miss, constructing a new instance")
    model = EmbeddingModel()
    _log_rss("immediately after the embedding-model load resolves")
    return model


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
        # v1.0.1 Decision 4 — the sample path reuses a precomputed relevance
        # assessment instead of calling the gate, and that must be visible rather
        # than looking like the check was quietly dropped. `source` distinguishes
        # the two real cases: "sidecar" is the precomputed result, "live" means
        # the staleness guard rejected the sidecar and the gate ran normally.
        sample_assessment = st.session_state.get("sample_assessment")
        if sample_assessment is not None:
            source = st.session_state.get("sample_assessment_source")
            marker = "precomputed" if source == "sidecar" else "live (sidecar rejected by guard)"
            st.caption(f"document relevance gate — {marker}")
            st.json(sample_assessment.model_dump())

        session: DefenseSession | None = st.session_state.get("session")
        if session is not None:
            st.caption(f"difficulty_current: {session.difficulty_current}/5")
            for i, turn in enumerate(session.turns, start=1):
                with st.expander(f"Turn {i} — Dr. {turn.panelist_name}"):
                    st.write(f"difficulty_level: {turn.difficulty_level}")
                    st.write(f"grounding_retry_used: {turn.grounding_retry_used}")
                    st.write(f"grounding_flagged: {turn.grounding_flagged}")
                    # Decision 4 extension — grounding_reference is retired from the
                    # main-flow transcript entirely and lives only here now.
                    st.write(f"grounding_reference: {turn.grounding_reference!r}")
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


def _avatar_inner_html(icon: str) -> str:
    """Avatar-slot content for an icon identifier (v0.3j amendment): a data-URI
    <img> when the identifier is a curated image stem in assets/icons/, the emoji
    glyph otherwise. Data URI rather than st.image because the avatar lives inside
    the card's raw-HTML markdown block; the files are curated repo assets a few KB
    each, so re-encoding per rerun is negligible."""
    path = image_icon_path(icon)
    if path is None:
        return html.escape(icon)
    suffix = path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg"}.get(suffix, suffix)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img src="data:image/{mime};base64,{encoded}" alt="{html.escape(icon)}">'


def _icon_choice_label(icon: str) -> str:
    """Picker label: emoji pass through as glyphs; image stems read as words."""
    if image_icon_path(icon) is None:
        return icon
    return icon.replace("_", " ").replace("-", " ").title()


def _icon_caption_prefix(icon: str) -> str:
    """Sidebar caption prefix: emoji glyphs inline fine; image icons don't (captions
    are plain text), so those slots get no prefix rather than a raw filename stem."""
    return "" if image_icon_path(icon) is not None else f"{icon} "


def _render_panelist_card(panelist: Panelist, state: str, status_line: str) -> str:
    # v0.3j Decision 3: the resolved icon replaces the letter initial in the avatar
    # slot — icons render here and in the sidebar mini roster, nowhere else.
    initial = _avatar_inner_html(panelist.icon)
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


def _render_panel_preview_card(archetype_key: str, custom_name: str = "", icon: str = "") -> str:
    """Panel-composition preview for the intake screen (Task 1, presentation
    only) — same `.ads-card` visual language as the live session's panel row.
    v0.3j: a slot with a custom name shows it; slots left blank keep the honest
    "Assigned at convene" placeholder (persona generation hasn't run). The avatar
    slot shows the chosen icon where one is picked, the title initial otherwise."""
    title = html.escape(_archetype_title(archetype_key))
    avatar = _avatar_inner_html(icon) if icon else html.escape(title[:1].upper())
    avatar_class = "ads-avatar da" if archetype_key == DEVILS_ADVOCATE_KEY else "ads-avatar"
    name_html = (
        f'<div class="ads-card-name">{html.escape(f"Dr. {custom_name}")}</div>'
        if custom_name
        else '<div class="ads-card-name ads-placeholder">Assigned at convene</div>'
    )
    return (
        f'<div class="ads-card">'
        f'<div class="{avatar_class}">{avatar}</div>'
        f"{name_html}"
        f'<div class="ads-card-title small-caps-label">{title}</div>'
        f"</div>"
    )


def _render_panel_preview_row(
    defense_type: DefenseType,
    other_subtype: OtherSubtype | None,
    selected_archetypes: list[str],
    document_id: str,
    customization_inputs: dict[str, tuple[str, str]] | None = None,
) -> None:
    """Live panel-composition preview (Task 1; v0.3e Decision 6 updates the input
    from a count to the user's actual picks) — `compose_full_roster` is a pure,
    instant, zero-LLM function (Decision 1's own source), so it's safe to call on
    every rerun. `domain`/`topic` are irrelevant to roster composition (only
    `defense_type`/`other_subtype`/`selected_archetypes` feed `compose_panel`'s
    lookup) — this profile is a disposable preview object, never stored, never the
    one that starts the session."""
    if selected_archetypes:
        preview_profile = DefenseProfile(
            defense_type=defense_type,
            other_subtype=other_subtype,
            domain="preview",
            topic="preview",
            selected_archetypes=selected_archetypes,
            document_id=document_id,
        )
        archetype_keys = compose_full_roster(preview_profile)
    else:
        # st.multiselect has no min-selection floor (no such parameter exists), so a
        # user can clear every domain pick before the submit-time "Select at least
        # one panelist" check below ever runs. DefenseProfile.selected_archetypes
        # enforces min_length=1 for the real session profile, rightly — but this
        # preview reruns on every keystroke and is disposable, so it must tolerate
        # the zero-selection state instead of raising a ValidationError on it.
        # Devil's Advocate is always seated regardless of domain selection.
        archetype_keys = [DEVILS_ADVOCATE_KEY]
    st.markdown('<p class="small-caps-label">Your panel — composed from the profile</p>', unsafe_allow_html=True)
    # Flex row instead of st.columns (bug fix): st.columns divides the row into
    # `len(archetype_keys)` equal-width slots with no floor, and nested one level
    # inside the bordered "Defense profile" container that width shrinks further
    # still — five columns there was narrow enough to force mid-word character
    # breaks ("Implementatio" / "n"). A flex-wrap row gives every card a real
    # min-width and wraps extra cards onto a second line instead of compressing.
    inputs = customization_inputs or {}
    cards_html = "".join(
        _render_panel_preview_card(
            key,
            custom_name=inputs.get(key, ("", ""))[0].strip(),
            icon=inputs.get(key, ("", ""))[1],
        )
        for key in archetype_keys
    )
    st.markdown(f'<div class="ads-preview-row">{cards_html}</div>', unsafe_allow_html=True)


def _turn_header(turn_num: int, panelist_name: str, archetype_key: str) -> str:
    """Permanent small-caps header (v0.3d Decision 8) — attribution lives in the
    transcript block itself, not a floating avatar, so it can't scroll away from
    its content."""
    return f"Turn {turn_num} — Dr. {panelist_name} · {_archetype_title(archetype_key)}"


def _turn_progress_label(session: DefenseSession) -> str:
    """v0.3f Decision 8: the old fixed turn-count constant is retired, so a
    'Turn N of 6' denominator would be misleading now that session length is
    variable — most sessions end well before the T_max backstop.
    Domain-panelists-heard-of-total is a real, honest ceiling (unlike the old
    fixed 6) that doesn't imply the session runs a fixed length."""
    domain_total = sum(1 for p in session.panel if p.archetype_key != DEVILS_ADVOCATE_KEY)
    domain_spoken = len(
        {t.panelist_archetype_key for t in session.turns if t.panelist_archetype_key != DEVILS_ADVOCATE_KEY}
    )
    return f"{domain_spoken}/{domain_total} panelists heard"


def _render_turn_content(turn) -> None:
    """Question + (once answered) the candidate's inset answer — the body shared
    by a collapsed-history block and the active turn block. `grounding_reference`
    never renders here (Decision 4 extension) — it lives in dev-view only."""
    st.write(turn.question)
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
    # v0.4a Decision 6, path 1 — checked first, before any question-generation call:
    # demo mode allows demo_max_turns() turns; the attempt to start turn N+1 is where
    # the cap fires, same shape as the LLMProviderError abort paths below it.
    if st.session_state.get("api_key_mode") == "demo" and turn_num > demo_max_turns():
        _abort_demo_limit()
        st.rerun()

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
                            _new_provider(LLM_STAGE_QUESTION_GENERATION),
                            session,
                            st.session_state.chunks,
                            st.session_state.embedding_model,
                            active_panelist,
                            st.session_state.other_subtype_line,
                            st.session_state.gemini_model,
                            label=LLM_STAGE_QUESTION_GENERATION,
                        )
                    except LLMProviderError as exc:
                        _abort(LLM_STAGE_QUESTION_GENERATION, exc)
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
        answer = st.text_area("Your answer", key=f"answer_{turn_num}")
        submitted = st.button("Submit answer", key=f"submit_{turn_num}")

    if submitted:
        if not answer.strip():
            st.warning("Answer cannot be blank — please respond.")
        else:
            # Perceived-latency fix (post-0.3 deploy bugfix pass): the spinner used to open
            # only around the score LLM call itself, after turn.answer assignment and lock
            # acquisition had already run — those are microseconds, not the 3-4s users saw,
            # but no visible feedback existed until Streamlit's next paint reached that inner
            # `with`. Opening the spinner here, before any of that, paints something within
            # milliseconds of the click. This does not make the score/report calls faster —
            # only when "loading" first becomes visible. The lock's critical section (recheck
            # -> score -> append -> clear pending_turn) is left exactly as it was; only the
            # spinner's boundary moved outward around it, so the concurrent-rerun guard is
            # unaffected. One side effect: on the session-final turn, this spinner and the
            # "Building end-of-session report..." spinner below are both active briefly (the
            # inner one nested inside the outer), so two loading lines show at once for that
            # one turn — a minor, disclosed cosmetic overlap, not a functional issue.
            with st.spinner("Scoring your answer..."):
                turn.answer = answer.strip()
                with _turn_lock:
                    # Re-check after acquiring the lock: if a concurrent (overlapping) rerun
                    # already scored and consumed this pending_turn, there's nothing left to do.
                    if st.session_state.pending_turn is not None:
                        try:
                            turn.score = _call_with_timeout(
                                _score_answer,
                                _new_provider(LLM_STAGE_ANSWER_SCORING),
                                turn,
                                active_panelist,
                                session.profile.defense_type.value,
                                label=LLM_STAGE_ANSWER_SCORING,
                            )
                        except LLMProviderError as exc:
                            _abort(LLM_STAGE_ANSWER_SCORING, exc)
                            st.rerun()

                        session.turns.append(turn)
                        if len(session.turns) == 1:
                            _log_rss("after the first fully scored turn of the session")
                        session.difficulty_current = _clamp_difficulty(
                            session.difficulty_current + turn.score.difficulty_delta
                        )
                        st.session_state.pending_turn = None

                        if session_is_complete(session):
                            # Driver-level wire-up (v0.3 hardening, Task 1d): same call CLI's
                            # `main()` already makes at session-end. Zero changes to
                            # `report.py` — orchestration only. v0.3f: the old fixed-turn-count
                            # check is replaced by `engine.session_is_complete` (Decision 5),
                            # engine.py's own termination condition — this driver call is the
                            # minimal wiring needed for that condition to have any effect on
                            # a real session, not a redesign of this file's scope.
                            try:
                                with st.spinner("Building end-of-session report..."):
                                    session.report = _call_with_timeout(
                                        build_report,
                                        session,
                                        _new_provider(LLM_STAGE_REPORT_NARRATIVE),
                                        _new_suggestions_provider(),
                                        label=LLM_STAGE_REPORT_BUILD,
                                    )
                            except LLMProviderError as exc:
                                _abort(LLM_STAGE_REPORT_BUILD, exc)
                                st.rerun()
                            else:
                                st.session_state.stage = "done"
                                _persist(SessionStage.COMPLETED)
                        else:
                            # v0.4b Decision 3: save fires after every completed turn,
                            # not just at session end — a crash loses at most the
                            # in-flight turn.
                            _persist(SessionStage.IN_PROGRESS)
                            time.sleep(
                                MODEL_CALL_DELAY_SECONDS.get(st.session_state.gemini_model, DEFAULT_CALL_DELAY)
                            )
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

    # v1.0b-2 Decision 3: one compact section, turn number + suggestion text only —
    # no repeated question/answer/subscores per turn (that was the rejected Option A).
    st.markdown('<p class="small-caps-label">Answer suggestions</p>', unsafe_allow_html=True)
    if report.suggestions_fallback_used:
        st.write("Suggestions unavailable this session")
    elif not report.answer_suggestions:
        st.write("No suggestions for this session")
    else:
        for suggestion in report.answer_suggestions:
            st.markdown(f"**Turn {suggestion.turn_index + 1}:** {suggestion.suggestion}")


# v1.0.1 Appendix B (B3), superseding Decision 5's original banner string. Held as
# a module constant rather than inlined so the locked wording has exactly one home
# and a test can assert it verbatim — the copy is a decision, not an implementation
# detail, and drift here is the kind of thing nobody notices until it ships.
EXAMPLE_BANNER = (
    "**Recorded example session — not live.**\n\n"
    "A complete defense against the sample capstone. The free demo is capped at "
    "four turns; use your own API key for a full session."
)


def _render_example_stage() -> None:
    """The read-only example view (v1.0.1 Decision 5): the committed fixture
    rendered through the same `_render_exchange_history` and `_render_report`
    every real session uses. No parallel render path — if this view is right,
    those renderers are right, which is the self-verifying property Decision 5
    picked it for.

    Read-only means what Decision 5 says it means: no answer box, no submit
    control, no dev-view toggle (suppressed at the sidebar call site below),
    and exactly one CTA back to intake.

    The fixture is *not* loaded into `st.session_state.session`. Keeping it in a
    local means the case-file sidebar stays empty here (it keys off that exact
    slot), and — more importantly — no live-session code path can ever find a
    recorded session sitting in the place it expects a real one."""
    try:
        persisted = load_example_session()
    except ExampleSessionUnavailable as exc:
        # A broken fixture degrades to a message and the route out. It must never
        # take down the screen, because intake is the only path to a real session.
        logger.error("example session unavailable: %s", exc)
        st.error("The recorded example session isn't available right now.")
        if st.button("Back to intake", key="example_back_unavailable"):
            st.session_state.stage = "intake"
            st.rerun()
        return

    session = persisted.session

    with st.container(key="example_banner"):
        st.markdown(EXAMPLE_BANNER)

    st.subheader(f"Recorded session — {len(session.turns)} turns")
    st.caption(
        f"{session.profile.defense_type.value} · {session.profile.domain} · "
        f"{_turn_progress_label(session)}"
    )

    _render_exchange_history(session)

    # Guaranteed non-None by `load_example_session`, which rejects a fixture
    # without a report rather than letting this view render half a session.
    assert session.report is not None
    _render_report(session.report)

    # A plain stage flip, not `_reset()`: the example view never writes session
    # state, so returning the visitor to exactly the intake they left — key mode
    # already chosen, document still loaded if they had one — is both correct and
    # the smaller action. `_reset()` here would silently cost a demo visitor their
    # resolved key mode for having looked at a recording.
    if st.button("Back to intake", key="example_back", type="primary"):
        st.session_state.stage = "intake"
        st.rerun()


def _render_analytics_nav_sidebar() -> None:
    """v1.0b: a small, always-visible sidebar link — not folded into the dev-view
    toggle (analytics is a real end-user feature, not a diagnostics tool) and not
    an inline button on the intake page (Sean's ask: keep the main flow uncluttered).
    Renders nothing when persistence is off (nothing to link to at all) or the
    analytics view is already showing; otherwise always renders, even with zero
    completed sessions — deliberate departure from `_render_resume_section`'s
    empty-means-absent convention (v1.0a amendment, 2026-07-24): on a Cloud
    deploy where `sessions/` may be permanently empty, hiding the entry point
    would make the whole v1.0b feature look like it doesn't exist. Clicking
    through with zero sessions lands on `_render_analytics_view`'s own empty
    state, which explains itself."""
    if not _persistence_enabled() or st.session_state.stage == "analytics":
        return
    with st.sidebar:
        if st.button("View practice analytics", key="goto_analytics", width="stretch"):
            st.session_state.stage = "analytics"
            st.rerun()


def _render_analytics_view() -> None:
    """v1.0b: cross-session analytics screen — four zero-cost pure-aggregation
    views (Decision 1) plus one cached, on-demand gap-theme clustering call
    (Decision 2). Reads `sessions/*.json` directly via `analytics.load_completed_sessions`
    — independent of the resume list above, which reads in-progress sessions only."""
    st.subheader("Practice analytics")

    sessions, skips = analytics.load_completed_sessions()

    if not sessions:
        st.write("No completed sessions yet — analytics will appear once you finish a defense.")
        if st.button("Back", key="analytics_back"):
            st.session_state.stage = "intake"
            st.rerun()
        return

    st.caption(f"{len(sessions)} completed session(s)" + (f" · {len(skips)} skipped" if skips else ""))

    st.markdown('<p class="small-caps-label">Session list</p>', unsafe_allow_html=True)
    list_rows = analytics.session_list_view(sessions)
    st.table(
        [
            {
                "Date": row["date"].strftime("%Y-%m-%d %H:%M UTC"),
                "Type": row["defense_type"],
                "Topic": row["topic"],
                "Clarity": f"{row['overall_avg_clarity']:.2f}",
                "Depth": f"{row['overall_avg_depth']:.2f}",
                "Grounding": f"{row['overall_avg_grounding']:.2f}",
                "Turns": row["turn_count"],
            }
            for row in list_rows
        ]
    )

    st.markdown('<p class="small-caps-label">Quality trend</p>', unsafe_allow_html=True)
    trend_rows = analytics.quality_trend_view(sessions)
    trend_df = pd.DataFrame(trend_rows).set_index("session_sequence")
    st.line_chart(trend_df[["clarity", "depth", "grounding"]])

    st.markdown('<p class="small-caps-label">Difficulty trajectory</p>', unsafe_allow_html=True)
    for row in analytics.difficulty_trajectory_view(sessions):
        st.caption(f"Session {row['session_sequence']} — {row['topic']}")
        st.line_chart(pd.DataFrame({"difficulty": row["trajectory"]}))

    st.markdown('<p class="small-caps-label">Pushback outcomes</p>', unsafe_allow_html=True)
    pushback_rows = analytics.pushback_outcome_trend_view(sessions)
    pushback_df = pd.DataFrame(pushback_rows).set_index("session_sequence")
    st.bar_chart(pushback_df[["recovered", "held", "deteriorated"]])

    st.markdown('<p class="small-caps-label">Recurring gap themes</p>', unsafe_allow_html=True)
    primary_gaps = analytics.all_primary_gaps(sessions)
    if not primary_gaps:
        st.write("No gaps recorded yet.")
    else:
        cache_key = analytics.gap_theme_cache_key(primary_gaps)
        with st.spinner("Looking for recurring themes..."):
            theme_analysis = _cached_cluster_gap_themes(cache_key, _new_analytics_provider())
        if theme_analysis is None:
            st.write("Theme analysis unavailable this session")
        else:
            for theme in theme_analysis.themes:
                st.markdown(f"**{theme.theme_label}** ({theme.occurrence_count})")
                for gap in theme.supporting_gaps:
                    st.caption(f"- {gap}")

    if skips:
        for skip in skips:
            st.caption(f"Skipped {skip.path.name} — {skip.reason}")

    if st.button("Back", key="analytics_back"):
        st.session_state.stage = "intake"
        st.rerun()


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


def _ingest_and_extract(uploaded_file, *, skip_relevance_check: bool = False) -> None:
    """Ingestion + extraction only (Decision 4) — the defense profile form renders
    below the dropzone on the same 'intake' stage once `document_id` is set here
    (Sean's combined-page ask); this function never advances `stage` itself, so
    a rerun after it just re-renders 'intake' with the profile section now
    visible. `document_id`'s presence is what gates that, so this never re-fires
    for the same document.

    v0.3g Brief: a relevance gate runs after chunking, before embedding — no reason
    to spend embedding time on a document the user may abandon at the warning. A
    negative assessment stores itself in `st.session_state.relevance_warning` and
    returns without setting `document_id`, same non-advancing shape as the
    ingestion-error path above it; the caller (intake UI) renders the warning and a
    "Proceed anyway" button, which re-calls this function with
    `skip_relevance_check=True` — chunk_pdf is pure/cheap re-parsing, not an LLM
    call, so redoing it rather than stashing raw text across reruns keeps this
    function self-contained. `skip_relevance_check=True` is also correct for a
    second "Process document" click, but that's not the path used today — see
    the intake UI, which explicitly clears `relevance_warning` on every fresh
    "Process document" click, so a re-upload always gets a fresh check."""
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

    if not skip_relevance_check:
        with st.spinner("Checking document relevance..."):
            assessment = assess_document(texts, _new_relevance_provider())
        if not assessment.is_defense_material:
            st.session_state.relevance_warning = assessment
            return

    embedding_model = _load_embedding_model()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]

    settings = load_settings()

    with st.spinner("Extracting domain/topic from the document..."):
        extraction = extract_document_profile(texts, _new_provider(LLM_STAGE_EXTRACTION))

    st.session_state.document_id = str(uuid4())
    st.session_state.uploaded_filename = uploaded_file.name
    st.session_state.page_count = page_count
    st.session_state.chunks = chunks
    st.session_state.embedding_model = embedding_model
    st.session_state.gemini_model = settings.gemini_model
    st.session_state.extracted_domain = extraction.domain
    st.session_state.extracted_topic = extraction.topic


def _load_sample_and_extract() -> None:
    """The sample-document path (v1.0.1 Decisions 2/4). Same post-conditions as
    `_ingest_and_extract` — `document_id` set, chunks/page count/extraction in
    session state — reached without an upload and, when the sidecar is current,
    without the chunking and document-embedding passes.

    Two differences from the upload path, both deliberate. The relevance gate is
    not called when the sidecar is current: the assessment is precomputed and
    stored in the manifest (Decision 4), so the sample path spends one fewer
    gemini-2.5-flash call per session. And if the guard rejects the sidecar, the
    loader has already re-ingested live and returns `assessment=None` — a stale
    manifest's assessment is never reused, so the gate fires here instead and the
    optimisation degrades to the ordinary upload behaviour."""
    embedding_model = _load_embedding_model()
    sample = load_sample_document(embedding_model)

    assessment = sample.assessment
    if assessment is None:
        with st.spinner("Checking document relevance..."):
            assessment = assess_document([c.text for c in sample.chunks], _new_relevance_provider())

    settings = load_settings()

    with st.spinner("Extracting domain/topic from the document..."):
        extraction = extract_document_profile(
            [c.text for c in sample.chunks], _new_provider(LLM_STAGE_EXTRACTION)
        )

    st.session_state.document_id = str(uuid4())
    st.session_state.uploaded_filename = SAMPLE_DISPLAY_NAME
    st.session_state.page_count = sample.page_count
    st.session_state.chunks = sample.chunks
    st.session_state.embedding_model = embedding_model
    st.session_state.gemini_model = settings.gemini_model
    st.session_state.extracted_domain = extraction.domain
    st.session_state.extracted_topic = extraction.topic
    # Dev-view only (Decision 4's transparency requirement): the gate must not
    # look absent on the sample path just because it did not fire this session.
    st.session_state.sample_assessment = assessment
    st.session_state.sample_assessment_source = sample.source


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
                st.caption(f"{_icon_caption_prefix(panelist.icon)}Dr. {panelist.panelist_name} — {turns_taken} {noun} asked")
        else:
            active_panelist = select_active_panelist(session)
            for panelist in session.panel:
                _, status_line = _panelist_card_state(panelist, session, active_panelist)
                st.caption(f"{_icon_caption_prefix(panelist.icon)}Dr. {panelist.panelist_name} — {status_line}")

        st.markdown('<p class="sidebar-section-label">Session</p>', unsafe_allow_html=True)
        st.caption(f"{_turn_progress_label(session)} · {session.profile.defense_type.value}")


def _resume_session(persisted: PersistedSession) -> None:
    """v0.4b Decision 5: load -> rebuild transcript UI (free — `session.turns` is
    already the full transcript, rendered by the same `_render_exchange_history`
    every running session uses) -> restore live state (free — difficulty/speaker/
    retention counters are all derived from `session.turns`+`session.panel`, not
    separately stored) -> re-embed chunks locally -> next turn proceeds. This
    function does the state restoration only; the caller reruns afterward."""
    embedding_model = _load_embedding_model()
    with st.spinner("Re-embedding document..."):
        embeddings = embedding_model.encode(persisted.document_chunks)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(persisted.document_chunks, embeddings)]

    session = persisted.session
    settings = load_settings()
    st.session_state.session = session
    st.session_state.session_id = persisted.session_id
    st.session_state.session_created_at = persisted.created_at
    st.session_state.chunks = chunks
    st.session_state.embedding_model = embedding_model
    st.session_state.gemini_model = settings.gemini_model
    st.session_state.other_subtype_line = (
        f"\n- Defense subtype: {session.profile.other_subtype.value}"
        if session.profile.other_subtype is not None
        else ""
    )
    # personas_fallback_used is not part of PersistedSession's contents (v0.4b
    # Decision 2) — it's an export-only cosmetic flag (panel.py: a fallback persona
    # "degrades aesthetics only, not the transcript"), never regenerated on resume
    # (the roster itself is restored verbatim). Defaulted rather than expanding the
    # locked schema for a field with no functional effect.
    st.session_state.personas_fallback_used = False
    st.session_state.pending_turn = None
    st.session_state.stage = "running"


def _render_resume_section() -> None:
    """Intake-screen resume list (v0.4b Decision 4): in-progress sessions only,
    document name / last-updated / progress per row, Resume and Delete (confirm via
    popover). Renders nothing when there's nothing to show — an empty sessions
    directory (or the directory not existing yet) means this whole section is
    absent, not an empty placeholder."""
    sessions, skips = persistence.list_sessions()
    if not sessions and not skips:
        return

    st.markdown('<p class="small-caps-label">Resume a saved session</p>', unsafe_allow_html=True)
    for persisted in sessions:
        session = persisted.session
        turn_num = len(session.turns) + 1
        with st.container(border=True, key=f"session_row_{persisted.session_id}"):
            title_col, resume_col, delete_col = st.columns([6, 1.4, 1], vertical_alignment="center")
            with title_col:
                st.markdown(
                    f'<p class="ads-session-title">{html.escape(session.profile.topic)}</p>'
                    f'<p class="ads-session-meta">'
                    f"Updated {persisted.updated_at.strftime('%Y-%m-%d %H:%M UTC')} · "
                    f"turn {turn_num} · {_turn_progress_label(session)}</p>",
                    unsafe_allow_html=True,
                )
            with resume_col:
                if st.button("Resume", key=f"resume_{persisted.session_id}", type="primary"):
                    _resume_session(persisted)
                    st.rerun()
            with delete_col:
                with st.popover("Delete", key=f"session_delete_{persisted.session_id}"):
                    st.write("Delete this saved session permanently? This can't be undone.")
                    if st.button("Confirm delete", key=f"confirm_delete_{persisted.session_id}"):
                        persistence.delete_session(persisted.session_id)
                        st.rerun()
    for skip in skips:
        st.caption(f"Skipped {skip.path.name} — {skip.reason}")
    st.divider()


# v1.0.1 Decision 5: the example stage is read-only, and "no dev-view toggle" is
# part of what read-only means there — so the three sidebar renderers are skipped
# wholesale rather than each growing its own stage check. The case-file sidebar
# would render nothing anyway (no `session` in session state) and the analytics
# link is off on any deployed instance, but suppressing all three at one call site
# is what actually makes the rule legible.
if st.session_state.stage != "example":
    _render_dev_view()
    _render_case_file_sidebar()
    _render_analytics_nav_sidebar()

if st.session_state.stage == "example":
    _render_example_stage()

elif st.session_state.stage == "intake":
    # One combined page (Sean's ask): the dropzone and the defense-profile form
    # live on the same screen, matching the design prototype's composition. The
    # profile section only appears once `document_id` exists — that's the real
    # signal ingestion + extraction finished, not a separate navigated-to stage.
    #
    # Bug fix (transitional-window leak): a blocking call (_ingest_and_extract,
    # generate_panel) that runs inside a widget's own script pass does NOT clear
    # anything already rendered earlier in that SAME pass — Streamlit only
    # replaces old elements once a fresh script pass (st.rerun()) actually
    # completes. Concretely, everything below used to render fully (dropzone/
    # summary, defense-profile card, preview cards, CTA button), THEN a spinner
    # got appended underneath it, and both sat on screen together for the whole
    # duration of the blocking call — confirmed live (screenshot: "Assembling
    # the panel..." spinner directly below a still-fully-rendered intake page).
    # `intake_slot` (a single st.empty()) lets both blocking-call sites clear
    # the whole intake UI immediately, before the call starts, instead of
    # leaving stale content sitting above the spinner.
    #
    # v1.0.1 Decision 7 — positioning copy, above the mode radio so it clears the
    # fold on a phone. The wording is locked in the decisions doc and rendered from
    # module constants rather than composed here: changing it is a decision, not an
    # implementation detail.
    _render_positioning_copy()

    # v0.4a Decision 1: the key/mode gate renders above all of the above and
    # everything below is unreachable until it resolves — st.stop() here is the
    # smallest-diff way to enforce that without re-nesting this whole 200-line
    # block inside an `if key_ready:`.
    if not _render_key_gate():
        st.stop()

    # v0.4b Decision 4: the resume list sits above the fresh-upload flow, and is
    # itself gated on the persistence flag — off means `_render_resume_section`
    # is never even called, so `persistence.list_sessions()` never runs and no
    # `sessions/` directory is ever touched (Decision 1 parity).
    if _persistence_enabled():
        _render_resume_section()

    document_ready = "document_id" in st.session_state
    intake_slot = st.empty()

    with intake_slot.container():
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
            process_clicked = st.button("Process document", type="primary", disabled=uploaded_file is None)

            # v1.0.1 Decision 5 (as amended): the sample control lives *inside*
            # the upload step, as an alternative to choosing a file — not as a
            # shortcut on the intake screen. A visitor still passes the key-mode
            # gate and the positioning copy before reaching a question; this
            # only removes the need to have a PDF of your own to hand.
            st.markdown(
                '<p style="text-align:center;color:#8A8378;font-size:0.8rem;margin:0.35rem 0;">'
                "or</p>",
                unsafe_allow_html=True,
            )
            sample_clicked = st.button("Try a sample capstone", width="stretch")
            st.caption("A synthetic capstone written for this demo — no upload needed.")

            # v0.3g Brief: soft relevance gate — a negative assessment from a prior
            # "Process document" click stays visible (in session_state) until either a
            # fresh "Process document" click re-checks it or "Proceed anyway" bypasses
            # it, user-facing by design (no dev-view gating needed).
            relevance_warning = st.session_state.get("relevance_warning")
            proceed_anyway_clicked = False
            if relevance_warning is not None:
                st.warning(
                    f"This looks like a {relevance_warning.document_kind}, not research "
                    "material. A defense session against it won't be meaningful. — "
                    f"{relevance_warning.reason}"
                )
                proceed_anyway_clicked = st.button("Proceed anyway")

            # v1.0.1 Decision 5 (as amended): the example-session control sits
            # *below* the upload step as a subordinate secondary control, and must
            # not compete with the upload affordance or the primary CTA — hence a
            # tertiary (link-styled) button, not a bordered one. It renders only
            # while there is no document loaded: once a visitor has one ingested
            # they are committed to a real session, and an offer to go read a
            # recording instead is clutter at best.
            #
            # It gains full prominence in exactly one place — the demo-exhausted
            # gate in `_render_key_gate`, where it is the only thing left to offer.
            if st.button("See a recorded example session", key="goto_example_intake", type="tertiary"):
                st.session_state.stage = "example"
                st.rerun()
        else:
            process_clicked = False
            proceed_anyway_clicked = False
            sample_clicked = False
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
                    other_subtype = st.selectbox(
                        "Subtype", list(OtherSubtype), format_func=lambda ost: ost.value
                    )

                domain_col, topic_col = st.columns(2)
                with domain_col:
                    domain = st.text_input("Domain / discipline", value=st.session_state.extracted_domain)
                with topic_col:
                    topic = st.text_input("Research title / topic", value=st.session_state.extracted_topic)

                type_roster = PANEL_COMPOSITION[_composition_key(defense_type, other_subtype)]
                # Default: top 3 in natural (priority) order — st.multiselect returns
                # selections in option-list order regardless of click order, so this is
                # already natural order; compose_panel (v0.3e Decision 4) re-derives
                # natural order from PANEL_COMPOSITION independently either way.
                selected_archetypes = st.multiselect(
                    "Panel",
                    options=type_roster,
                    default=type_roster[:3],
                    format_func=_archetype_title,
                    max_selections=3,
                )
                # Panel caption (v0.3d Decision 1, updated v0.3e) — compose_full_roster
                # appends Devil's Advocate unconditionally, so the visible panel is always
                # one bigger than the selection; state the arithmetic explicitly rather
                # than let it read as a bug.
                st.markdown(
                    f'<p class="small-caps-label">{len(selected_archetypes)} domain panelists + '
                    f"Devil's Advocate = {len(selected_archetypes) + 1} total</p>",
                    unsafe_allow_html=True,
                )
                # v1.1a Decision 6 — static applicability note only, no content-based gating
                # (that's v1.1b). Statistical & Data Analysis and Results & Conclusions both
                # depend on document content that may not exist yet at intro+methodology+
                # implementation stage.
                st.caption(
                    "Select Statistical & Data Analysis or Results & Conclusions only if your "
                    "document contains those sections."
                )

                # v0.3j Decision 1 — per-slot customization rows. The intake widgets are
                # NOT inside an st.form (plain widgets + a regular button), so these rows
                # can track the multiselect reactively — the smallest-diff path of the
                # two the decision doc allowed; no restructuring needed. One row per
                # selected archetype in speaking order, plus the always-seated DA row.
                # Widget keys are archetype-scoped so a slot's entries survive unrelated
                # reruns and vanish with the slot when it's deselected.
                #
                # v0.3j amendment (2026-07-20): the rows live in a default-collapsed
                # expander (Sean: open-by-default cluttered the intake screen), and the
                # icon picker is fed by the curated-image registry in panel.py — image
                # stems first, emoji as the standing fallback. The preview row below the
                # expander stays always-visible, so a collapsed expander still shows the
                # customized result.
                customization_inputs: dict[str, tuple[str, str]] = {}
                preview_keys = [a for a in type_roster if a in selected_archetypes] + [DEVILS_ADVOCATE_KEY]
                icon_choices = list_icon_choices()
                with st.expander("Customize your panel — optional", expanded=False):
                    for archetype_key in preview_keys:
                        default_icon = archetype_default_icon(archetype_key)
                        title_col, name_col, icon_col = st.columns([2, 2, 1], vertical_alignment="center")
                        with title_col:
                            st.markdown(
                                f'<p class="small-caps-label" style="margin:0;">'
                                f"{html.escape(_archetype_title(archetype_key))}</p>",
                                unsafe_allow_html=True,
                            )
                        with name_col:
                            custom_name = st.text_input(
                                "Panelist name",
                                key=f"panelist_name_{archetype_key}",
                                placeholder="Name (optional) — “Dr.” is added automatically",
                                label_visibility="collapsed",
                            )
                        with icon_col:
                            # v0.3j amendment 2 — game-style icon select. The slot keeps
                            # its current choice in plain session state (no widget owns
                            # the key anymore); the popover frames the full curated grid,
                            # one click selects. Stale stems (file renamed/removed since
                            # the choice) fall back to the archetype default.
                            state_key = f"panelist_icon_{archetype_key}"
                            current_icon = st.session_state.get(state_key, default_icon)
                            if current_icon not in icon_choices:
                                current_icon = default_icon
                            thumb_col, pick_col = st.columns([1, 2], vertical_alignment="center")
                            with thumb_col:
                                current_path = image_icon_path(current_icon)
                                if current_path is not None:
                                    st.image(str(current_path), width=40)
                                else:
                                    st.markdown(
                                        f'<div style="font-size:1.6rem;text-align:center;">'
                                        f"{html.escape(current_icon)}</div>",
                                        unsafe_allow_html=True,
                                    )
                            with pick_col:
                                with st.popover("Change", width="stretch"):
                                    st.markdown(
                                        f'<p class="small-caps-label">Choose an icon — '
                                        f"{html.escape(_archetype_title(archetype_key))}</p>",
                                        unsafe_allow_html=True,
                                    )
                                    grid_width = 4
                                    for row_start in range(0, len(icon_choices), grid_width):
                                        grid_cols = st.columns(grid_width, gap="small")
                                        for cell, stem in zip(
                                            grid_cols, icon_choices[row_start : row_start + grid_width]
                                        ):
                                            with cell:
                                                stem_path = image_icon_path(stem)
                                                if stem_path is not None:
                                                    st.image(str(stem_path), width="stretch")
                                                else:
                                                    st.markdown(
                                                        f'<div style="font-size:2rem;text-align:center;">'
                                                        f"{html.escape(stem)}</div>",
                                                        unsafe_allow_html=True,
                                                    )
                                                if stem == current_icon:
                                                    st.button(
                                                        "✓",
                                                        key=f"pick_{archetype_key}_{stem}",
                                                        disabled=True,
                                                        width="stretch",
                                                        help=f"{_icon_choice_label(stem)} — selected",
                                                    )
                                                elif st.button(
                                                    _icon_choice_label(stem),
                                                    key=f"pick_{archetype_key}_{stem}",
                                                    width="stretch",
                                                    help=_icon_choice_label(stem),
                                                ):
                                                    st.session_state[state_key] = stem
                                                    st.rerun()
                        customization_inputs[archetype_key] = (custom_name, current_icon)

                _render_panel_preview_row(
                    defense_type,
                    other_subtype,
                    selected_archetypes,
                    st.session_state.document_id,
                    customization_inputs,
                )

                # v0.3j Decision 4 — difficulty_start was schema-validated (1-5,
                # default 2) since v0.3e but never had a form control. The caption
                # names that adaptation exists; the live meter stays dev-view only
                # (never-surfaced-mid-session rule untouched).
                difficulty_start = st.select_slider(
                    "Starting difficulty", options=[1, 2, 3, 4, 5], value=2
                )
                st.caption("How hard the panel opens. It adapts from there based on your answers.")

            _, cta_col, _ = st.columns([1, 1, 1])
            with cta_col:
                start_clicked = st.button("Convene the Panel", type="primary", width="stretch")
        else:
            start_clicked = False

    if process_clicked:
        # Deliberately NOT pre-cleared like the Convene path below: on ingestion
        # failure, _ingest_and_extract shows st.error() and returns without
        # setting document_id — the dropzone/button must stay on screen so the
        # user can retry, and no rerun should fire (a rerun would immediately
        # wipe the just-shown error before anyone could read it).
        #
        # v0.3g Brief: a fresh "Process document" click always re-runs the relevance
        # check (clearing any stale warning from a previous upload first) — only
        # "Proceed anyway" below bypasses it. A rejection sets relevance_warning but
        # not document_id — same transitional-window issue as the success path above:
        # `intake_slot.container()` already rendered (using the pre-click, just-cleared
        # None value) earlier in this same script pass, so without an explicit rerun
        # here too, the warning would sit in session_state and never reach the browser.
        st.session_state.relevance_warning = None
        # v0.3h Brief: the gate call (inside _ingest_and_extract, below) is the first
        # LLM call of the whole document->session->report journey, so the counter is
        # created here, at the earliest point any call can happen, and the SAME object
        # keeps accumulating through extraction, persona generation, and every later
        # turn/report call — that's what makes the gate call show up joined into the
        # session tally at export time rather than as a separate figure (Decision 1).
        # A fresh "Process document" click always starts a new tally, discarding any
        # prior click's partial (e.g. gate-only, rejected-and-abandoned) count for a
        # different document — each click is accounting for one candidate document's
        # journey, not a running total across abandoned attempts.
        st.session_state.llm_call_counter = CallCounter()
        with st.spinner("Processing document..."):
            _ingest_and_extract(uploaded_file)
        if "document_id" in st.session_state or st.session_state.relevance_warning is not None:
            st.rerun()

    if sample_clicked:
        # Same counter lifecycle as a "Process document" click (v0.3h Brief): the
        # tally is created at the earliest point any call can happen and keeps
        # accumulating through extraction, persona generation, and every turn. On
        # the sample path the gate call is normally absent from that tally — it was
        # spent once, offline, when the sidecar was built.
        st.session_state.relevance_warning = None
        st.session_state.llm_call_counter = CallCounter()
        with st.spinner("Loading the sample capstone..."):
            _load_sample_and_extract()
        if "document_id" in st.session_state:
            st.rerun()

    if proceed_anyway_clicked:
        # v0.3g Brief: bypasses the relevance check for this already-assessed upload
        # only — re-chunks the same file (cheap, pure-Python) rather than the gate,
        # then continues to embedding/extraction exactly as the pass path would.
        st.session_state.relevance_warning = None
        with st.spinner("Processing document..."):
            _ingest_and_extract(uploaded_file, skip_relevance_check=True)
        if "document_id" in st.session_state:
            st.rerun()

    if start_clicked:
        if not domain.strip() or not topic.strip():
            st.error("Domain and topic are required.")
        elif not selected_archetypes:
            st.error("Select at least one panelist.")
        else:
            intake_slot.empty()
            # v0.3j: a slot only becomes a PanelistCustomization if the user actually
            # customized it (non-blank name or non-default icon) — untouched slots
            # stay off the profile entirely, keeping the export honest about what
            # was user-chosen vs. generated/default.
            panel_customizations = []
            for archetype_key, (name_value, icon_value) in customization_inputs.items():
                display_name = name_value.strip() or None
                icon = icon_value if icon_value != archetype_default_icon(archetype_key) else None
                if display_name is not None or icon is not None:
                    panel_customizations.append(
                        PanelistCustomization(
                            archetype_key=archetype_key, display_name=display_name, icon=icon
                        )
                    )
            profile = DefenseProfile(
                defense_type=defense_type,
                other_subtype=other_subtype,
                domain=domain.strip(),
                topic=topic.strip(),
                selected_archetypes=selected_archetypes,
                difficulty_start=difficulty_start,
                document_id=st.session_state.document_id,
                panel_customizations=panel_customizations,
            )
            with st.spinner("Assembling the panel..."):
                archetype_roster = compose_full_roster(profile)
                panel, fallback_used = generate_panel(
                    profile, archetype_roster, _new_provider(LLM_STAGE_PERSONA_GENERATION)
                )

            st.session_state.session = DefenseSession(
                profile=profile, panel=panel, difficulty_current=profile.difficulty_start
            )
            st.session_state.personas_fallback_used = fallback_used
            st.session_state.other_subtype_line = (
                f"\n- Defense subtype: {other_subtype.value}" if other_subtype is not None else ""
            )
            st.session_state.pending_turn = None
            st.session_state.stage = "running"
            # v0.4b Decision 2: session_id is minted here, at session start — before
            # any save has fired (the first save is turn 1's completion, per
            # Decision 3). Only minted when the flag is on; `_persist` no-ops
            # without it, which is also how flag-off stays a true no-op end to end.
            if _persistence_enabled():
                st.session_state.session_id = str(uuid4())
                st.session_state.session_created_at = datetime.now(timezone.utc)
            st.rerun()

elif st.session_state.stage == "running":
    session: DefenseSession = st.session_state.session
    turn_num = len(session.turns) + 1
    active_panelist = select_active_panelist(session)

    if st.session_state.get("save_failed"):
        if st.session_state.get("save_failed_reason") == "stale_class":
            st.caption(
                "Couldn't save progress — code changed during this session. Restart the app for saving to resume."
            )
        else:
            st.caption("Couldn't save progress — session continues, resume may be unavailable.")

    # Difficulty is deliberately absent here (v0.3d Decision 4, item 3) — it never
    # renders in the main flow mid-session, only in the report's trajectory after the
    # session ends. It remains visible in dev-view (`_render_dev_view` above).
    st.subheader(f"Turn {turn_num} — {_turn_progress_label(session)}")

    _render_panel_row(session, active_panelist)
    _render_exchange_history(session)
    _render_current_exchange(session, active_panelist, turn_num)

elif st.session_state.stage == "aborted":
    st.error(ABORT_MESSAGES[st.session_state.abort_stage])
    # v0.4a Decision 6, path 2: own-key mid-session provider failures get copy
    # naming the likely cause (their key's own rate limit or revocation) instead
    # of reading as an app problem — every LLM-stage abort is a candidate (the
    # demo-cap key is mode=="demo" by construction, so this can't double up with
    # the demo copy above it).
    if (
        st.session_state.get("api_key_mode") == "own"
        and st.session_state.abort_stage != DEMO_TURN_CAP_ABORT_STAGE
    ):
        st.caption(
            "This is more likely to be your own key's rate limit or an expired/revoked "
            "key than an app problem — check it at aistudio.google.com/apikey."
        )
    if st.button("Start a new session", key="reset_from_aborted"):
        _reset()
        st.rerun()

elif st.session_state.stage == "done":
    session = st.session_state.session
    st.success(f"Session complete — {len(session.turns)} turns.")

    if session.report is not None:
        _render_report(session.report)

    # PROMPT_VERSION, personas_fallback_used, and llm_call_count are stamped only here,
    # at export time — DefenseSession stays free of any coupling to the prompts module,
    # generation provenance, or call accounting. This JSON is the seed of v1.0 analytics
    # and the artifact format for future eval runs.
    #
    # v0.3h Brief, Task 2: llm_call_count.total/by_stage come from the session's
    # CallCounter (accumulated since intake, see _new_provider) — the gate call is
    # folded into this total (Decision 1: joined at convene time, not reported
    # separately) rather than broken out, so this one number is the full per-session
    # call cost. Export-only — not surfaced anywhere in the UI.
    call_counter: CallCounter = st.session_state.llm_call_counter
    export_payload = {
        "prompt_version": PROMPT_VERSION,
        "personas_fallback_used": st.session_state.personas_fallback_used,
        "llm_call_count": {"total": call_counter.total, "by_stage": call_counter.by_stage},
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

elif st.session_state.stage == "analytics":
    _render_analytics_view()
