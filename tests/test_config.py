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
