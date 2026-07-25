"""Gemini implementation of the LLM provider."""

from __future__ import annotations

import collections
import logging
import os
import time
from typing import Callable, Type, TypeVar

from pydantic import BaseModel, ValidationError

from academic_defense_simulator.llm.provider import CallCounter, LLMProvider, LLMProviderError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECONDS = 2
_REQUEST_TIMEOUT_MS = 30_000  # a stalled request must fail into the existing retry path,
# not hang the caller indefinitely.

# v1.0a B'': sliding-window RPM limiter, scoped to every real network attempt (not just
# the grounding-retry loop). Live evidence against PathClear (evidence/
# v1.0a-pathclear-rpm-verification.txt) showed the ORDINARY one-question-gen-plus-one-
# score turn cadence alone already breaches the real 15 RPM free-tier ceiling once the
# embedded per-call sleep engine.py used to have is gone (confirmed by a genuine 429
# from Google, not just a computed estimate) -- grounding retries make it worse but are
# not the sole cause, so a limiter scoped only to retries would not have closed the gap.
#
# Module-level state, not per-instance: every real call site constructs a fresh
# GeminiProvider (streamlit_app.py's _new_provider is called anew on every Streamlit
# rerun), so instance-level state would reset before it ever accumulated anything --
# and the real Google quota is per-project-per-model, not per-object, so module-level
# state is the correct semantic match regardless.
_RPM_WINDOW_SECONDS = 60.0
# _RPM_CEILING is NOT a literal RPM cap -- it's a tuned knob, verified empirically, not
# derived by arithmetic from the tier limit. Observed peak (evidence/
# v1.0a-pathclear-rpm-verification.txt) runs consistently one call ABOVE the configured
# ceiling (14 -> peak 15, 13 -> peak 14), most likely a sliding-vs-fixed window boundary
# effect between this limiter's own admission-time bookkeeping and completion-time
# external measurement. 13 is the verified-clean setting against the real 15 RPM
# free-tier limit -- a ceiling of 14 still let peak touch exactly 15, the same number
# that produced a real 429 pre-fix. DO NOT raise this to 15 on the assumption it maps
# directly to a stated tier limit -- that reproduces the 429. Any tier change
# (different model, different quota) re-verifies empirically against a real session,
# the same way this number was reached, not by arithmetic against the documented limit.
_RPM_CEILING = {
    "gemini-3.1-flash-lite": 13,
}
_DEFAULT_RPM_CEILING = 13  # unrecognized model: stay conservative, not permissive (same
# convention as engine.DEFAULT_CALL_DELAY).

_call_timestamps: dict[str, "collections.deque[float]"] = collections.defaultdict(collections.deque)


def _throttle_for_rpm(model: str) -> None:
    """Sleeps zero whenever the rolling 60s window still has room for this model;
    once it's genuinely full, sleeps only the exact deficit until the oldest call in
    the window ages out, then proceeds -- never a flat unconditional delay."""
    now = time.monotonic()
    window = _call_timestamps[model]
    while window and now - window[0] >= _RPM_WINDOW_SECONDS:
        window.popleft()

    ceiling = _RPM_CEILING.get(model, _DEFAULT_RPM_CEILING)
    if len(window) >= ceiling:
        sleep_for = _RPM_WINDOW_SECONDS - (now - window[0])
        if sleep_for > 0:
            time.sleep(sleep_for)
        now = time.monotonic()
        while window and now - window[0] >= _RPM_WINDOW_SECONDS:
            window.popleft()

    window.append(now)


def _request_timeout_ms() -> int:
    """v1.0a item 6: `ADS_LLM_TIMEOUT_SECONDS`, read at call time (same
    read-on-every-call convention `demo_counter._env_int` uses, not baked in at
    import) so a live-forced value takes effect without a process restart.
    Default is `_REQUEST_TIMEOUT_MS` unchanged when unset or unparseable —
    existing behavior is preserved exactly. Lives here, not `config.py`: this
    knob only means anything to the Gemini transport, same reasoning that keeps
    all `google.genai` specifics isolated to this module."""
    raw = os.getenv("ADS_LLM_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _REQUEST_TIMEOUT_MS
    try:
        return int(float(raw) * 1000)
    except ValueError:
        return _REQUEST_TIMEOUT_MS


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

        timeout_ms = _request_timeout_ms()
        # v1.0a item 6 (B1): ADS_LLM_TIMEOUT_SECONDS falls back to the hardcoded
        # default silently on garbage/unparseable input — logging the RESOLVED
        # value here means a typo'd Cloud secret shows up as "still 30000" in
        # logs, not as a mysteriously-not-firing timeout guard.
        logger.info("Gemini provider timeout resolved to %d ms", timeout_ms)
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms)
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

        _throttle_for_rpm(self._model)
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
        _throttle_for_rpm(self._model)
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


def validate_gemini_key(api_key: str) -> bool:
    """v0.4a Decision 2: validates a user-supplied key via `client.models.list()` —
    the cheapest possible 'is this a real key' probe, no generate-call quota spent
    (unlike a minimal generate call, which would burn the user's own daily
    allowance on every paste). Auth failure, malformed key, and network errors all
    collapse to False — Decision 2's UI shows one plain rejection message either
    way, so this boundary doesn't need to distinguish them. No call_counter/label:
    this call is never part of a session's LLM-call tally (it costs no generate
    quota) and isn't attributable to a turn/stage."""
    import httpx
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    try:
        client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=_request_timeout_ms())
        )
        next(iter(client.models.list()), None)
        return True
    except (genai_errors.APIError, httpx.HTTPError):
        return False
