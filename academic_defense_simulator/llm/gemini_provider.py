"""Gemini implementation of the LLM provider."""

from __future__ import annotations

import time
from typing import Callable, Type, TypeVar

from pydantic import BaseModel, ValidationError

from academic_defense_simulator.llm.provider import CallCounter, LLMProvider, LLMProviderError

T = TypeVar("T", bound=BaseModel)

_RETRY_BACKOFF_SECONDS = 2
_REQUEST_TIMEOUT_MS = 30_000  # a stalled request must fail into the existing retry path,
# not hang the caller indefinitely.


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        *,
        call_counter: CallCounter | None = None,
        label: str = "unlabeled",
    ) -> None:
        from google import genai
        from google.genai import types

        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS)
        )
        self._model = model
        # v0.3h Brief: optional so every existing construction site (scripts, CLI,
        # tests) keeps working unchanged — call counting is pure observation, not a
        # required dependency of the provider.
        self._call_counter = call_counter
        self._label = label

    def _record_call(self) -> None:
        """Called once per actual network attempt (see `_call_once`/`_call_once_text`),
        not once per `generate_structured`/`generate_text` invocation — an attempt that
        raises still went over the wire and still counts."""
        if self._call_counter is not None:
            self._call_counter.record(self._label)

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

        self._record_call()
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

    def generate_text(self, prompt: str) -> str:
        """Plain-text call (v0.3c report narrative — Decision 3/4). Same retry-once
        shape as generate_structured, plus an empty/whitespace-only response is treated
        as a failure (Decision 4), not a successful empty string."""
        import httpx
        from google.genai import errors as genai_errors

        def call() -> str:
            return self._call_once_text(prompt)

        try:
            return call()
        except genai_errors.APIError as exc:
            if exc.code == 429:
                raise LLMProviderError(
                    "Gemini API returned 429 despite confirmed request pacing — this is "
                    "real quota exhaustion, not a pacing bug."
                ) from exc
            return self._retry_text_or_fail(call, "Gemini API error", exc)
        except httpx.TimeoutException as exc:
            return self._retry_text_or_fail(call, "Gemini API request timed out", exc)
        except ValueError as exc:
            return self._retry_text_or_fail(call, "Gemini returned an empty narrative response", exc)

    def _call_once_text(self, prompt: str) -> str:
        self._record_call()
        response = self._client.models.generate_content(model=self._model, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise ValueError("empty or whitespace-only response")
        return text

    def _retry_text_or_fail(self, call: Callable[[], str], label: str, first_exc: Exception) -> str:
        time.sleep(_RETRY_BACKOFF_SECONDS)
        try:
            return call()
        except Exception as retry_exc:
            raise LLMProviderError(
                f"{label} — retried once and failed again. (first: {first_exc!r}, retry: {retry_exc!r})"
            ) from retry_exc
