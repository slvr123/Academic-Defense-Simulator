"""v2.0 M17: a visitor's own Gemini key (docs/v2.0-m17-decisions.md).

The key arrives in the `X-Gemini-Key` header, on POST /keys/check and on the upload,
never as a form field. The upload's key lives on the in-memory session as a SecretStr
and builds that defense's providers; it dies with the session. It's never logged,
returned or stored.

Gemini's errors are read here by their shape (an exception in the cause chain with an
int `code` and a str `status`, which is google.genai's APIError), so this module never
imports google.genai: that stays in v1's gemini_provider.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Type, TypeVar

from fastapi import HTTPException, Request
from pydantic import BaseModel, SecretStr

from academic_defense_simulator.llm.provider import (
    LLMProvider,
    LLMProviderError,
    LLMQuotaExhaustedError,
)
from backend.errors import CodedHTTPException

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("backend.own_key")

KEY_HEADER = "X-Gemini-Key"

INVALID_KEY_MESSAGE = "Google rejected this key. Check for missing characters, or create a fresh one."
OWN_KEY_REJECTED_MESSAGE = (
    "Your Gemini key was rejected partway through. Paste a fresh key to start a new defense."
)
OWN_KEY_LIMITED_MESSAGE = (
    "Your Gemini key hit its own limit. Wait a minute and try again, or come back after midnight Pacific."
)
OWN_KEY_RETRY_AFTER_S = 60

# Whether Google accepts a key: one models.list call, no generation. v1's
# validate_gemini_key in production; tests inject a fake.
KeyChecker = Callable[[str], bool]

# Google's statuses for "this model isn't available to this project" (Decision 3).
_MODEL_UNAVAILABLE_CODES = frozenset({403, 404})


def read_key(request: Request) -> SecretStr | None:
    """The header's key, or None when it's absent or blank."""
    raw = request.headers.get(KEY_HEADER, "").strip()
    return SecretStr(raw) if raw else None


def invalid_key() -> CodedHTTPException:
    return CodedHTTPException(400, INVALID_KEY_MESSAGE, "invalid_key")


def _api_error(exc: BaseException) -> BaseException | None:
    """The Gemini API error behind `exc`, if there is one. v1's provider raises
    LLMProviderError `from` the APIError of its last attempt."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(getattr(current, "code", None), int) and isinstance(getattr(current, "status", None), str):
            return current
        current = current.__cause__ or current.__context__
    return None


def gemini_error_code(exc: BaseException) -> tuple[int, str] | None:
    """(code, status) of the Gemini API error behind `exc`, if there is one."""
    error = _api_error(exc)
    return None if error is None else (getattr(error, "code"), getattr(error, "status"))


def _is_auth_failure(exc: BaseException) -> bool:
    error = _api_error(exc)
    if error is None:
        return False
    code, status = getattr(error, "code"), getattr(error, "status")
    if code in (401, 403):
        return True
    # Google answers a bad or revoked key with 400 INVALID_ARGUMENT, "API key not valid".
    message = str(getattr(error, "message", "") or "")
    return code == 400 and status == "INVALID_ARGUMENT" and "api key" in message.lower()


def own_key_failure(exc: LLMProviderError) -> HTTPException | None:
    """Decision 4: the coded error for an own-key defense's failed call, or None to
    fall through to the server-key mapping (a 503 overload stays panel_busy)."""
    if isinstance(exc, LLMQuotaExhaustedError):
        headers = {"Retry-After": str(OWN_KEY_RETRY_AFTER_S)}
        return CodedHTTPException(429, OWN_KEY_LIMITED_MESSAGE, "own_key_limited", headers=headers)
    if _is_auth_failure(exc):
        return CodedHTTPException(403, OWN_KEY_REJECTED_MESSAGE, "own_key_rejected")
    return None


class SuggestionsWithFallback(LLMProvider):
    """Decision 3: answer suggestions on `primary` (gemini-2.5-flash), retried once on
    `fallback` (the defense model) when Google says the model isn't available to this
    key's project. Any other failure, a 503 included, raises as before."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider, fallback_model: str) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_model = fallback_model

    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        try:
            return self._primary.generate_structured(prompt, response_model)
        except LLMProviderError as exc:
            self._fall_back_or_raise(exc)
            return self._fallback.generate_structured(prompt, response_model)

    def generate_text(self, prompt: str) -> str:
        try:
            return self._primary.generate_text(prompt)
        except LLMProviderError as exc:
            self._fall_back_or_raise(exc)
            return self._fallback.generate_text(prompt)

    def _fall_back_or_raise(self, exc: LLMProviderError) -> None:
        found = gemini_error_code(exc)
        if found is None or found[0] not in _MODEL_UNAVAILABLE_CODES:
            raise exc
        # The reason only: Google's code and status, never the message or the key.
        logger.warning("suggestions fallback to=%s reason=%d %s", self._fallback_model, found[0], found[1])
