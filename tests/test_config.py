"""ADS_PERSISTENCE_ENABLED flag tests (v0.4b Brief step 3) — pure env-var parsing,
zero LLM calls. GEMINI_API_KEY must be set for `load_settings()` to return at all
(pre-existing requirement, unrelated to this flag); tests set it explicitly via
monkeypatch so they don't depend on a real `.env`.
"""

from __future__ import annotations

import logging

import pytest

from academic_defense_simulator.config import TTS_PROVIDER_BROWSER, TTS_PROVIDER_MIMO, load_settings


@pytest.fixture(autouse=True)
def _real_gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")


def test_persistence_defaults_off_when_unset(monkeypatch):
    """S.3 fix (2026-07-31): `load_settings()` calls `load_dotenv()` internally,
    which repopulates any env var absent from the process but present in the
    local `.env` file. A bare `delenv` here was therefore not simulating "unset"
    at all on a machine whose `.env` sets `ADS_PERSISTENCE_ENABLED` — this test
    had been silently failing-and-tolerated on exactly that kind of machine since
    v1.2, which is what rendered a live `Settings` repr into a pytest assertion
    diff and leaked a real credential (2026-07-31 Phase 2 session). Patching
    `load_dotenv` to a no-op makes "unset" mean unset regardless of the local
    `.env`'s contents, so this test's result no longer depends on which machine
    runs it."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("ADS_PERSISTENCE_ENABLED", raising=False)
    assert load_settings().persistence_enabled is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_persistence_enabled_on_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ADS_PERSISTENCE_ENABLED", value)
    assert load_settings().persistence_enabled is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_persistence_disabled_on_falsy_values(monkeypatch, value):
    monkeypatch.setenv("ADS_PERSISTENCE_ENABLED", value)
    assert load_settings().persistence_enabled is False


# ---------------------------------------------------------------------------
# v1.2.2 Decision 2 (retires v1.2.1 Decision 3's "unset means browser" for the
# unset case): ADS_TTS_PROVIDER unset resolves against MIMO_API_KEY presence —
# mimo when the key exists, browser when it doesn't. Explicit "browser" always
# wins regardless of key. Explicit "mimo" with no key falls back to browser,
# same as unset with no key — the key, not the literal env value, is what
# actually gates whether Mimo is reachable. Any other value falls back to
# browser with a warning logged, never raises.
# ---------------------------------------------------------------------------


def _no_dotenv_interference(monkeypatch):
    """S.3's fix, needed by every test below that `delenv`s something this
    machine's real `.env` also sets (`ADS_TTS_PROVIDER=mimo`, `MIMO_API_KEY`) —
    without it, `load_dotenv()` inside `load_settings()` silently repopulates
    the "unset" value from disk and the test stops testing what it claims to."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)


def test_tts_provider_defaults_to_mimo_when_unset_and_key_present(monkeypatch):
    """v1.2.2 Decision 2's new behavior: the previous version of this test
    asserted unset always means browser. It no longer does."""
    _no_dotenv_interference(monkeypatch)
    monkeypatch.delenv("ADS_TTS_PROVIDER", raising=False)
    monkeypatch.setenv("MIMO_API_KEY", "test-mimo-key-not-real")
    assert load_settings().tts_provider == TTS_PROVIDER_MIMO


def test_tts_provider_defaults_to_browser_when_unset_and_key_absent(monkeypatch):
    """The no-key path: Task 4's "assert on the no-key path instead" applies
    here as much as to the integration-level render test."""
    _no_dotenv_interference(monkeypatch)
    monkeypatch.delenv("ADS_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    assert load_settings().tts_provider == TTS_PROVIDER_BROWSER


def test_tts_provider_browser_recognized_even_when_key_is_present(monkeypatch):
    """Explicit browser overrides the key-presence default — proven by setting
    a key too and confirming it changes nothing."""
    monkeypatch.setenv("ADS_TTS_PROVIDER", "BROWSER")
    monkeypatch.setenv("MIMO_API_KEY", "test-mimo-key-not-real")
    assert load_settings().tts_provider == TTS_PROVIDER_BROWSER


def test_tts_provider_mimo_recognized_when_key_is_present(monkeypatch):
    monkeypatch.setenv("ADS_TTS_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-mimo-key-not-real")
    assert load_settings().tts_provider == TTS_PROVIDER_MIMO


def test_explicit_mimo_without_key_resolves_to_browser_with_warning(monkeypatch, caplog):
    """v1.2.2 Decision 2's other half: "key absence must resolve to browser
    with no failed network call — do not default to mimo and rely on
    fail-open to catch it." Explicit `ADS_TTS_PROVIDER=mimo` does not get a
    pass just because it's explicit; no key still means browser."""
    _no_dotenv_interference(monkeypatch)
    monkeypatch.setenv("ADS_TTS_PROVIDER", "mimo")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        settings = load_settings()
    assert settings.tts_provider == TTS_PROVIDER_BROWSER
    assert "MIMO_API_KEY is not set" in caplog.text


def test_unknown_tts_provider_falls_back_to_browser_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("ADS_TTS_PROVIDER", "definitely-not-a-real-provider")
    with caplog.at_level(logging.WARNING):
        settings = load_settings()
    assert settings.tts_provider == TTS_PROVIDER_BROWSER
    assert "definitely-not-a-real-provider" in caplog.text
