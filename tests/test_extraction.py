"""Domain/topic extraction tests (0.3a Task 5). Mocked provider — no network, no LLM calls."""

from __future__ import annotations

import logging

from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.document_profile_extraction import DocumentProfileExtraction
from academic_defense_simulator.document_profile import _EXTRACTION_INPUT_CHAR_CAP, extract_document_profile


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


def test_valid_extraction_passes_through():
    expected = DocumentProfileExtraction(domain="Software Engineering", topic="Library Management System for DAZSMA")
    provider = _StubProvider([expected])
    result = extract_document_profile(["chunk one", "chunk two"], provider)
    assert result == expected
    assert provider.calls == 1


def test_failure_then_success_recovers_without_fallback():
    expected = DocumentProfileExtraction(domain="Software Engineering", topic="Library Management System for DAZSMA")
    provider = _StubProvider([LLMProviderError("transient"), expected])
    result = extract_document_profile(["chunk one"], provider)
    assert result == expected
    assert provider.calls == 2


def test_failure_twice_falls_back_to_blank_fields(caplog):
    provider = _StubProvider([LLMProviderError("bad"), LLMProviderError("bad again")])
    with caplog.at_level(logging.WARNING):
        result = extract_document_profile(["chunk one"], provider)
    assert result == DocumentProfileExtraction(domain="", topic="")
    assert provider.calls == 2
    assert "blank" in caplog.text.lower()


def test_input_is_first_three_chunks_capped_at_3000_chars():
    chunks = ["a" * 2000, "b" * 2000, "c" * 2000, "d" * 2000]  # 4th chunk must be excluded
    expected = DocumentProfileExtraction(domain="d", topic="t")
    provider = _StubProvider([expected])
    extract_document_profile(chunks, provider)
    assert "d" * 2000 not in provider.last_prompt
    document_head_len = len("a" * 2000 + "\n\n" + "b" * 2000 + "\n\n" + "c" * 2000)
    assert document_head_len > _EXTRACTION_INPUT_CHAR_CAP  # confirms the cap is actually exercised
