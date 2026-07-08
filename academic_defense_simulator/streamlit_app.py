"""Minimal Streamlit wrap — thin I/O layer over the existing session loop.

Reuses `main.py`'s turn-loop helpers and `DefenseSession` as-is; no changes to
`retrieve()`, prompt templates, or scoring logic. `panel_size` and
`difficulty_start` stay hardcoded (Pydantic defaults on `DefenseProfile`), not
exposed as controls yet.
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
import time
from uuid import uuid4

import streamlit as st

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.main import (
    DEFAULT_CALL_DELAY,
    MAX_TURNS,
    MODEL_CALL_DELAY_SECONDS,
    _ACTIVE_ARCHETYPE,
    _clamp_difficulty,
    _generate_question,
    _score_answer,
)
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PROMPT_VERSION
from academic_defense_simulator.rag.chunking import DocumentIngestionError, chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

st.set_page_config(page_title="Academic Defense Simulator")
st.title("Academic Defense Simulator")

if "stage" not in st.session_state:
    st.session_state.stage = "intake"


def _reset() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.stage = "intake"


def _ingest_document(uploaded_file, defense_type, other_subtype, domain, topic) -> None:
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
    profile = DefenseProfile(
        defense_type=defense_type,
        other_subtype=other_subtype,
        domain=domain.strip(),
        topic=topic.strip(),
        document_id=str(uuid4()),
    )

    st.session_state.chunks = chunks
    st.session_state.embedding_model = embedding_model
    st.session_state.provider = GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
    st.session_state.gemini_model = settings.gemini_model
    st.session_state.session = DefenseSession(profile=profile, difficulty_current=profile.difficulty_start)
    st.session_state.other_subtype_line = (
        f"\n- Defense subtype: {other_subtype.value}" if other_subtype is not None else ""
    )
    st.session_state.pending_turn = None
    st.session_state.stage = "running"


if st.session_state.stage == "intake":
    st.subheader("Start a defense session")

    defense_type = st.selectbox("Defense type", list(DefenseType), format_func=lambda dt: dt.value)
    other_subtype = None
    if defense_type == DefenseType.OTHER:
        other_subtype = st.selectbox("Subtype", list(OtherSubtype), format_func=lambda ost: ost.value)
    domain = st.text_input("Domain / discipline")
    topic = st.text_input("Research title / topic")
    uploaded_file = st.file_uploader("Upload your research document (PDF)", type=["pdf"])

    if st.button("Start Session", type="primary"):
        if not domain.strip() or not topic.strip() or uploaded_file is None:
            st.error("Domain, topic, and a PDF upload are all required.")
        else:
            with st.spinner("Processing document..."):
                _ingest_document(uploaded_file, defense_type, other_subtype, domain, topic)
            if st.session_state.stage == "running":
                st.rerun()

elif st.session_state.stage == "running":
    session: DefenseSession = st.session_state.session
    turn_num = len(session.turns) + 1

    st.subheader(f"Turn {turn_num}/{MAX_TURNS} — difficulty {session.difficulty_current}/5")

    if st.session_state.pending_turn is None:
        with st.spinner("Dr. Reyes is preparing a question..."):
            try:
                archetype = ARCHETYPE_CONFIG[_ACTIVE_ARCHETYPE]
                st.session_state.pending_turn = _generate_question(
                    st.session_state.provider,
                    session,
                    st.session_state.chunks,
                    st.session_state.embedding_model,
                    archetype,
                    st.session_state.other_subtype_line,
                    st.session_state.gemini_model,
                )
            except LLMProviderError as exc:
                st.session_state.stage = "aborted"
                st.session_state.abort_message = str(exc)
                st.rerun()

    turn = st.session_state.pending_turn
    if turn is not None:
        st.markdown(f"**Dr. Reyes:** {turn.question}")
        st.caption(f'Grounding: "{turn.grounding_reference}"')
        answer = st.text_area("Your answer", key=f"answer_{turn_num}")

        if st.button("Submit answer"):
            if not answer.strip():
                st.warning("Answer cannot be blank — please respond.")
            else:
                turn.answer = answer.strip()
                with st.spinner("Scoring your answer..."):
                    try:
                        archetype = ARCHETYPE_CONFIG[_ACTIVE_ARCHETYPE]
                        turn.score = _score_answer(
                            st.session_state.provider, turn, archetype, session.profile.defense_type.value
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

    # PROMPT_VERSION is stamped only here, at export time — DefenseSession stays free of
    # any coupling to the prompts module. This JSON is the seed of v1.0 analytics and the
    # artifact format for future eval runs.
    export_payload = {
        "prompt_version": PROMPT_VERSION,
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
