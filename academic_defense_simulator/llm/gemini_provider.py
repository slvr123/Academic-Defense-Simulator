"""Gemini implementation of the LLM provider."""

from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from academic_defense_simulator.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_model,
            ),
        )
        return response_model.model_validate_json(response.text)
