"""Environment-driven configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default scoring/question model. flash-lite for iterative dev (500 RPD free-tier vs
# 2.5-flash's 20 RPD); override GEMINI_MODEL=gemini-2.5-flash for final verification runs.
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"

# v1.2.1 Decision 3: opt-in, env-only, absent in the deployed environment, fail-safe
# on misconfiguration — same shape as ADS_PERSISTENCE_ENABLED below.
TTS_PROVIDER_BROWSER = "browser"
TTS_PROVIDER_MIMO = "mimo"


class Secret:
    """Wraps a credential so it cannot leak by accident through any of the ordinary
    ways a value turns into text: `repr()`, `str()`, an f-string, `%s`/`.format()`
    logging, or an exception message built from `%r`/`!r`. Only `.get()` returns
    the real value — every other route is masked.

    Replaces an earlier `field(repr=False)` patch on `Settings.mimo_api_key`, which
    covered only the dataclass-generated `__repr__`. A real `MIMO_API_KEY` still
    leaked in plaintext through a pytest assertion diff (2026-07-31) — `repr=False`
    does nothing for `str()`, and pytest's diff renderer calls both. A real
    `GEMINI_API_KEY` leaked the same session through a follow-up verification
    command that printed a `Settings` instance directly. Neither failure is
    Mimo-specific; both are fixed here by making the value itself resist rendering,
    not by hiding one field from one renderer.

    Call `.get()` only at the actual network-call boundary (inside the provider
    module that sends the value over the wire) — every other layer should pass the
    `Secret` object itself around unopened, so an exception raised anywhere else in
    the call chain renders the mask, not the value.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented


@dataclass(frozen=True)
class Settings:
    gemini_api_key: Secret
    gemini_model: str
    persistence_enabled: bool
    tts_provider: str
    mimo_api_key: Secret


def load_settings() -> Settings:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - optional dependency.
        pass

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    # v0.4b Decision 1: local-disk session persistence, default off. Deployed
    # Streamlit Cloud never sets this, so it stays st.session_state-only exactly
    # as before this slice.
    persistence_enabled = os.getenv("ADS_PERSISTENCE_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    mimo_api_key = os.getenv("MIMO_API_KEY", "").strip()

    # v1.2.2 Decision 2 (retires v1.2.1 Decision 3's "unset means browser" for
    # the unset case only): explicit "browser" always means browser, regardless
    # of key presence. Explicit "mimo" or unset both resolve against whether
    # MIMO_API_KEY is actually present — a key-less "mimo" must never reach the
    # call site, because that would mean attempting (and fail-open catching) a
    # network call that was always going to fail for lack of credentials, once
    # per synthesis, forever. Any other value logs a warning and falls back to
    # browser rather than raising — misconfiguring this must never abort a
    # session, since audio is garnish (Decision 6).
    tts_provider_raw = os.getenv("ADS_TTS_PROVIDER", "").strip().lower()
    if tts_provider_raw == TTS_PROVIDER_BROWSER:
        tts_provider = TTS_PROVIDER_BROWSER
    elif tts_provider_raw in ("", TTS_PROVIDER_MIMO):
        if mimo_api_key:
            tts_provider = TTS_PROVIDER_MIMO
        else:
            if tts_provider_raw == TTS_PROVIDER_MIMO:
                logger.warning("ADS_TTS_PROVIDER=mimo but MIMO_API_KEY is not set — falling back to browser")
            tts_provider = TTS_PROVIDER_BROWSER
    else:
        logger.warning(
            "Unknown ADS_TTS_PROVIDER=%r — falling back to %r", tts_provider_raw, TTS_PROVIDER_BROWSER
        )
        tts_provider = TTS_PROVIDER_BROWSER

    return Settings(
        gemini_api_key=Secret(api_key),
        gemini_model=model,
        persistence_enabled=persistence_enabled,
        tts_provider=tts_provider,
        mimo_api_key=Secret(mimo_api_key),
    )
