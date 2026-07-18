"""Document relevance gate tests (v0.3g Brief). Mocked provider — no network, no LLM calls."""

from __future__ import annotations

import logging

from academic_defense_simulator.document_relevance import (
    _HEAD_SAMPLE_COUNT,
    _MIDDLE_SAMPLE_COUNT,
    _sample_chunks,
    assess_document,
)
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.document_assessment import DocumentAssessment


class _StubProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.last_prompt = None

    def generate_structured(self, prompt, response_model):
        self.calls += 1
        self.last_prompt = prompt
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_rejection_path_returns_negative_assessment():
    expected = DocumentAssessment(is_defense_material=False, document_kind="resume", reason="It's a resume.")
    provider = _StubProvider([expected])
    result = assess_document(["chunk one", "chunk two"], provider)
    assert result == expected
    assert result.is_defense_material is False
    assert provider.calls == 1


def test_pass_path_returns_positive_assessment():
    expected = DocumentAssessment(
        is_defense_material=True, document_kind="research paper", reason="Looks like a capstone report."
    )
    provider = _StubProvider([expected])
    result = assess_document(["chunk one", "chunk two"], provider)
    assert result == expected
    assert result.is_defense_material is True


def test_assessment_failure_fails_open(caplog):
    provider = _StubProvider([LLMProviderError("simulated API failure")])
    with caplog.at_level(logging.WARNING):
        result = assess_document(["chunk one"], provider)
    assert result.is_defense_material is True
    assert provider.calls == 1
    assert "fail" in caplog.text.lower()


def test_short_document_sampling_does_not_crash():
    # Fewer chunks than the sample budget (_HEAD_SAMPLE_COUNT + _MIDDLE_SAMPLE_COUNT) —
    # every chunk should be used, no IndexError, no crash.
    chunks = ["only chunk one", "only chunk two"]
    expected = DocumentAssessment(is_defense_material=True, document_kind="paper", reason="ok")
    provider = _StubProvider([expected])
    result = assess_document(chunks, provider)
    assert result == expected
    assert "only chunk one" in provider.last_prompt
    assert "only chunk two" in provider.last_prompt


def test_single_chunk_document_does_not_crash():
    expected = DocumentAssessment(is_defense_material=True, document_kind="paper", reason="ok")
    provider = _StubProvider([expected])
    result = assess_document(["the only chunk"], provider)
    assert result == expected


def test_sample_chunks_uses_head_and_middle():
    n = 20
    chunks = [f"chunk {i}" for i in range(n)]
    sampled = _sample_chunks(chunks)
    assert sampled[: _HEAD_SAMPLE_COUNT] == chunks[:_HEAD_SAMPLE_COUNT]
    mid_start = n // 2
    assert chunks[mid_start] in sampled
    assert len(sampled) <= _HEAD_SAMPLE_COUNT + _MIDDLE_SAMPLE_COUNT


def test_sample_chunks_short_document_returns_all():
    chunks = ["a", "b", "c"]
    assert _sample_chunks(chunks) == chunks
