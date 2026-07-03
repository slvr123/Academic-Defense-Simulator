"""Gemini implementation of the LLM provider."""

from __future__ import annotations

from academic_defense_simulator.llm.provider import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def generate(self, prompt: str) -> str:
        _ = prompt
        return ""
