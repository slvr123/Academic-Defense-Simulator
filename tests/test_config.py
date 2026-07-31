"""ADS_PERSISTENCE_ENABLED flag tests (v0.4b Brief step 3) — pure env-var parsing,
zero LLM calls. GEMINI_API_KEY must be set for `load_settings()` to return at all
(pre-existing requirement, unrelated to this flag); tests set it explicitly via
monkeypatch so they don't depend on a real `.env`.
"""

from __future__ import annotations

import pytest

from academic_defense_simulator.config import load_settings


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
