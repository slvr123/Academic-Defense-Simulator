"""Gemini implementation of the LLM provider."""

from __future__ import annotations

import time
from typing import Callable, Type, TypeVar

from pydantic import BaseModel, ValidationError

from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError

T = TypeVar("T", bound=BaseModel)

_RETRY_BACKOFF_SECONDS = 2


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        import httpx
        from google.genai import errors as genai_errors

        def call() -> T:
            return self._call_once(prompt, response_model)

        try:
            return call()
        except genai_errors.APIError as exc:
            if exc.code == 429:
                raise LLMProviderError(
                    "Gemini API returned 429 despite confirmed request pacing — this is "
                    "real quota exhaustion, not a pacing bug. Check quota or wait for "
                    "reset. Session aborted, no retry."
                ) from exc
            return self._retry_or_fail(call, "Gemini API error", exc)
        except httpx.TimeoutException as exc:
            return self._retry_or_fail(call, "Gemini API request timed out", exc)
        except ValidationError as exc:
            return self._retry_or_fail(
                call, "Gemini returned malformed/unparseable JSON", exc
            )

    def _call_once(self, prompt: str, response_model: Type[T]) -> T:
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

    def _retry_or_fail(self, call: Callable[[], T], label: str, first_exc: Exception) -> T:
        time.sleep(_RETRY_BACKOFF_SECONDS)
        try:
            return call()
        except Exception as retry_exc:
            raise LLMProviderError(
                f"{label} — retried once and failed again. Session aborted. "
                f"(first: {first_exc!r}, retry: {retry_exc!r})"
            ) from retry_exc
