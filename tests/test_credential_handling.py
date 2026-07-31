"""Credential-leak regression tests.

S.1-S.4 (2026-07-31, mid-`v1.2.1-mimo-tts-brief.md` Phase 2 session): a real
`MIMO_API_KEY` and a real `GEMINI_API_KEY` both appeared in plaintext in a Code
session transcript the same day. The first via pytest's own assertion-diff repr
of a `Settings` instance, from an unrelated pre-existing test failure
(`test_persistence_defaults_off_when_unset` in `test_config.py`, fixed in the
same session). The second via a verification command that printed a `Settings`
instance directly to confirm the first fix. Both keys were rotated immediately;
this file is what stops a third occurrence, for either credential — the defect
was never Mimo-specific, it was `Settings` being an ordinary dataclass with a
generated `__repr__` and no `__str__` override at all.

This file is also where Task 2.8's key-leak assertion (no `MIMO_API_KEY` value
in any persisted session artifact) lives once Phase 2 resumes — grouped here
deliberately, so every "a credential must never leak" invariant is in one place
rather than scattered across whichever module happened to touch a credential.
"""

from __future__ import annotations

import pytest

from academic_defense_simulator.config import Secret, load_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Every test here sets its own sentinel credentials rather than depending on
    a real `.env` — the whole point is to prove the values never render, so a
    sentinel that would be obvious in any output is more useful evidence than a
    real key would be, and it means these tests never need a real credential to
    run at all."""
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL_GEMINI_KEY)
    monkeypatch.setenv("MIMO_API_KEY", SENTINEL_MIMO_KEY)


SENTINEL_GEMINI_KEY = "AIzaSENTINEL_DO_NOT_SHIP_9876543210"
SENTINEL_MIMO_KEY = "mimo-sk-SENTINEL-DO-NOT-SHIP-1234567890"


def test_settings_repr_never_contains_a_credential_value():
    rendered = repr(load_settings())
    assert SENTINEL_GEMINI_KEY not in rendered
    assert SENTINEL_MIMO_KEY not in rendered


def test_settings_str_never_contains_a_credential_value():
    rendered = str(load_settings())
    assert SENTINEL_GEMINI_KEY not in rendered
    assert SENTINEL_MIMO_KEY not in rendered


def test_settings_fstring_never_contains_a_credential_value():
    """The exact failure mode that leaked a real key: something rendering a
    `Settings` instance through `str()` — an f-string, `%s` logging, or (as
    happened) pytest's own assertion-diff renderer, which uses the same
    machinery."""
    rendered = f"{load_settings()}"
    assert SENTINEL_GEMINI_KEY not in rendered
    assert SENTINEL_MIMO_KEY not in rendered


def test_secret_get_returns_the_real_value():
    """The one sanctioned way to read a credential still has to work — masking
    every other renderer must not also break the actual call sites that need
    the real value to make a network call."""
    secret = Secret(SENTINEL_MIMO_KEY)
    assert secret.get() == SENTINEL_MIMO_KEY
