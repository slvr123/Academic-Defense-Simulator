"""DEV-ONLY offline entrypoint: the real Streamlit app with every network call stubbed.

    streamlit run scripts/offline_stub_app.py

Never deploy this file. It exists so UI evidence (scripts/screenshot_sweep.py) can drive
every stage without a Gemini key, a MiMo key, or the sentence-transformers download.

How it works: before executing `academic_defense_simulator/streamlit_app.py`, it replaces
the module attributes the app imports by name, so the app picks up the stubs through its
own unchanged import lines. Every patch goes through `_patch`, which raises if the name no
longer exists on its module, so a rename in production code fails this script loudly
instead of silently letting a real call through. As a second line of defence, the Gemini
provider's two call methods, `validate_gemini_key` and MiMo `synthesize_speech` are
replaced with functions that raise, so anything the stubs miss shows up as an error
rather than as a network call.

What is stubbed:
- `engine._generate_question` / `engine._score_answer`: deterministic, realistic-length
  question text and a mid-range score.
- `document_profile.extract_document_profile`: fixed domain/topic.
- `document_relevance.assess_document`: always "defense material".
- `panel.generate_panel`: the static fallback roster (`FALLBACK_PANELISTS`, which covers
  every archetype since v1.3a Decision 5).
- `rag.embeddings.EmbeddingModel`: deterministic 384-dim unit vectors, no model download.
- `report.build_report` is NOT stubbed: it runs for real against the raising Gemini
  provider and takes its own narrative/suggestions fallback path.

Set ADS_TTS_PROVIDER=browser when running this; the MiMo stub raises if it is reached.
Set ADS_STUB_LATENCY_SECONDS (default 0) to make every stubbed LLM call sleep that long,
for reproducing races that need a call to be in flight.
"""

from __future__ import annotations

import hashlib
import math
import os
import runpy
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from academic_defense_simulator import (  # noqa: E402
    document_profile,
    document_relevance,
    engine,
    mimo_provider,
    panel,
)
from academic_defense_simulator.llm import gemini_provider  # noqa: E402
from academic_defense_simulator.llm.provider import LLMProviderError  # noqa: E402
from academic_defense_simulator.models.answer_score import AnswerScore  # noqa: E402
from academic_defense_simulator.models.document_assessment import DocumentAssessment  # noqa: E402
from academic_defense_simulator.models.document_profile_extraction import (  # noqa: E402
    DocumentProfileExtraction,
)
from academic_defense_simulator.models.session import ConversationTurn  # noqa: E402
from academic_defense_simulator.rag import embeddings  # noqa: E402

_STUB_QUESTION = (
    "Your evaluation captured only three test sessions with the pilot group. Walk me "
    "through why that sample is enough to support the usability claim in your "
    "conclusions, and what you would change if a reviewer asked for more."
)


def _latency() -> None:
    time.sleep(float(os.environ.get("ADS_STUB_LATENCY_SECONDS", "0") or 0))


def _patch(module, name: str, value) -> None:
    if not hasattr(module, name):
        raise RuntimeError(
            f"offline stub: {module.__name__}.{name} no longer exists — update "
            "scripts/offline_stub_app.py to match the production name"
        )
    setattr(module, name, value)


def _offline_failure(*_args, **_kwargs):
    raise LLMProviderError("offline stub mode: network call blocked")


def _fake_generate_question(provider, session, chunks, embedding_model, panelist, *_args, **_kwargs):
    _latency()
    return ConversationTurn(
        panelist_archetype_key=panelist.archetype_key,
        panelist_name=panelist.panelist_name,
        question=f"{_STUB_QUESTION} (stub turn {len(session.turns) + 1})",
        grounding_reference="stub grounding reference",
        chunk_index=len(session.turns) % max(1, len(chunks)),
        chunk_text=chunks[0].text if chunks else "stub chunk",
        difficulty_level=session.difficulty_current,
    )


def _fake_score_answer(provider, turn, panelist, defense_type):
    _latency()
    return AnswerScore(
        clarity=3, depth=3, grounding=3, difficulty_delta=0,
        primary_gap="stub gap", answer_summary="stub summary of the candidate's claim",
    )


def _fake_extract(chunks, provider):
    _latency()
    return DocumentProfileExtraction(domain="Computer Science", topic="Offline stub capstone")


def _fake_assess(chunks, provider):
    _latency()
    return DocumentAssessment(is_defense_material=True, document_kind="capstone project", reason="offline stub")


def _fake_generate_panel(profile, archetype_keys, provider):
    roster = [panel.FALLBACK_PANELISTS[key] for key in archetype_keys]
    return panel.apply_customizations(roster, profile), True


@dataclass
class _StubEmbeddingModel:
    """Same name and interface as `rag.embeddings.EmbeddingModel`, no model load."""

    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(text) for text in texts]


def _unit_vector(text: str, dims: int = 384) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [digest[i % len(digest)] - 127.5 for i in range(dims)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


_patch(engine, "_generate_question", _fake_generate_question)
_patch(engine, "_score_answer", _fake_score_answer)
_patch(document_profile, "extract_document_profile", _fake_extract)
_patch(document_relevance, "assess_document", _fake_assess)
_patch(panel, "generate_panel", _fake_generate_panel)
_patch(embeddings, "EmbeddingModel", _StubEmbeddingModel)
_patch(gemini_provider.GeminiProvider, "generate_structured", _offline_failure)
_patch(gemini_provider.GeminiProvider, "generate_text", _offline_failure)
_patch(gemini_provider, "validate_gemini_key", _offline_failure)
_patch(mimo_provider, "synthesize_speech", _offline_failure)

runpy.run_path(str(REPO_ROOT / "academic_defense_simulator" / "streamlit_app.py"), run_name="__main__")
