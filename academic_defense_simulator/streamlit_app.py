"""Minimal Streamlit wrap — thin I/O layer over the existing session loop.

Reuses `main.py`'s turn-loop helpers and `DefenseSession` as-is; no changes to
`retrieve()`, prompt templates, or scoring logic. Flow (0.3a Decision 4): upload ->
ingest -> extract (one LLM call, cached per document_id) -> profile form (prefilled,
editable) -> start session. `panel_size` is a form control, capped at the composition
list length for the selected defense type/subtype (0.3a Decision 1); `difficulty_start`
stays hidden/hardcoded (Pydantic default on `DefenseProfile`).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit (local `streamlit run` and Community Cloud alike) puts this script's own
# directory on sys.path, not the repo root — so `academic_defense_simulator.*` imports
# below fail with ModuleNotFoundError unless the repo root is added explicitly here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import os
import tempfile
import threading
import time
from uuid import uuid4

import streamlit as st

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.document_profile import extract_document_profile
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.main import (
    DEFAULT_CALL_DELAY,
    MAX_TURNS,
    MODEL_CALL_DELAY_SECONDS,
    _clamp_difficulty,
    _generate_question,
    _score_answer,
)
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import PANEL_COMPOSITION, compose_panel, generate_panel
from academic_defense_simulator.prompts.panelist_prompts import PROMPT_VERSION
from academic_defense_simulator.rag.chunking import DocumentIngestionError, chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

st.set_page_config(page_title="Academic Defense Simulator")
st.title("Academic Defense Simulator")

if "stage" not in st.session_state:
    st.session_state.stage = "upload"

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
    st.session_state.stage = "upload"


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


def _ingest_and_extract(uploaded_file) -> None:
    """Ingestion + extraction only (Decision 4) — no defense profile yet, that's
    collected on the next stage's form. Cached per document_id so re-rendering the
    form (stage 'profile') never re-triggers this LLM call."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name
        texts = chunk_pdf(tmp_path)
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
    st.session_state.chunks = chunks
    st.session_state.embedding_model = embedding_model
    st.session_state.gemini_model = settings.gemini_model
    st.session_state.extracted_domain = extraction.domain
    st.session_state.extracted_topic = extraction.topic
    st.session_state.stage = "profile"


if st.session_state.stage == "upload":
    st.subheader("Upload your research document")

    uploaded_file = st.file_uploader("Upload your research document (PDF)", type=["pdf"])

    if st.button("Process document", type="primary", disabled=uploaded_file is None):
        with st.spinner("Processing document..."):
            _ingest_and_extract(uploaded_file)
        if st.session_state.stage == "profile":
            st.rerun()

elif st.session_state.stage == "profile":
    st.subheader("Confirm defense details")

    defense_type = st.selectbox("Defense type", list(DefenseType), format_func=lambda dt: dt.value)
    other_subtype = None
    if defense_type == DefenseType.OTHER:
        other_subtype = st.selectbox("Subtype", list(OtherSubtype), format_func=lambda ost: ost.value)

    domain = st.text_input("Domain / discipline", value=st.session_state.extracted_domain)
    topic = st.text_input("Research title / topic", value=st.session_state.extracted_topic)

    max_panel_size = len(PANEL_COMPOSITION[_composition_key(defense_type, other_subtype)])
    panel_size = st.number_input(
        "Panel size", min_value=1, max_value=max_panel_size, value=max_panel_size
    )

    if st.button("Start Session", type="primary"):
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
                archetype_roster = compose_panel(profile)
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
    active_panelist = session.panel[0]

    st.subheader(f"Turn {turn_num}/{MAX_TURNS} — difficulty {session.difficulty_current}/5")

    if st.session_state.pending_turn is None:
        with _turn_lock:
            # Re-check after acquiring the lock: if a concurrent (overlapping) rerun got
            # here first and already finished, pending_turn is no longer None and this
            # rerun has nothing left to do.
            if st.session_state.pending_turn is None:
                with st.spinner(f"Dr. {active_panelist.panelist_name} is preparing a question..."):
                    try:
                        st.session_state.pending_turn = _generate_question(
                            _new_provider(),
                            session,
                            st.session_state.chunks,
                            st.session_state.embedding_model,
                            active_panelist,
                            st.session_state.other_subtype_line,
                            st.session_state.gemini_model,
                        )
                    except LLMProviderError as exc:
                        st.session_state.stage = "aborted"
                        st.session_state.abort_message = str(exc)
                        st.rerun()

    turn = st.session_state.pending_turn
    if turn is not None:
        st.markdown(f"**Dr. {active_panelist.panelist_name}:** {turn.question}")
        st.caption(f'Grounding: "{turn.grounding_reference}"')
        answer = st.text_area("Your answer", key=f"answer_{turn_num}")

        if st.button("Submit answer"):
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
                                turn.score = _score_answer(
                                    _new_provider(), turn, active_panelist, session.profile.defense_type.value
                                )
                            except LLMProviderError as exc:
                                st.session_state.stage = "aborted"
                                st.session_state.abort_message = str(exc)
                                st.rerun()

                        session.turns.append(turn)
                        session.difficulty_current = _clamp_difficulty(
                            session.difficulty_current + turn.score.difficulty_delta
                        )
                        st.session_state.pending_turn = None

                        if len(session.turns) >= MAX_TURNS:
                            st.session_state.stage = "done"
                        else:
                            time.sleep(MODEL_CALL_DELAY_SECONDS.get(st.session_state.gemini_model, DEFAULT_CALL_DELAY))
                st.rerun()

elif st.session_state.stage == "aborted":
    st.error(f"Session aborted: {st.session_state.abort_message}")
    if st.button("Start a new session", key="reset_from_aborted"):
        _reset()
        st.rerun()

elif st.session_state.stage == "done":
    session = st.session_state.session
    st.success(f"Session complete — {len(session.turns)} turns.")

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
