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


# ---------------------------------------------------------------------------
# Task 2.8 — Decision 4's key-leak assertion: "The v1.1 inventory-script
# regression check ... is extended to assert no MIMO_API_KEY value appears in
# any persisted artifact. Sentinel-key precedent: a rejected call from the
# provider is the evidence, not a grep."
#
# The live half of that precedent was run once outside this suite (2026-07-31,
# not re-run here — it needs a real network call and a genuine rejection isn't
# something to depend on in a test that runs on every commit):
# `mimo_provider.synthesize_speech` was called with sentinel key
# "mimo-sk-SENTINEL-DO-NOT-SHIP-20260731" and rejected by the real API
# (MimoTTSError raised) — proving the key genuinely went over the wire.
# `sessions/`, `evidence/`, and the whole git-tracked tree were then grepped
# for that sentinel: zero matches anywhere.
#
# What runs on every commit instead is the structural guarantee that live
# check depended on. Two guards, matching `scripts/inventory_committed_
# document_text.py`'s extended `_assert_no_credential_shaped_keys` check:
# ---------------------------------------------------------------------------


def test_persisted_session_schema_has_no_credential_or_audio_shaped_field():
    """`PersistedSession` (and its nested `DefenseSession`/`ConversationTurn`)
    is the entire persisted-artifact surface — `persistence.py` serializes
    exactly one `PersistedSession` per file, and `example_session.py`'s
    committed fixture is the same shape. None of their field names should ever
    suggest a credential or a raw audio payload; if one does, Decision 4 has
    regressed, whether or not anyone thinks to check its actual value."""
    from academic_defense_simulator.models.session import ConversationTurn, DefenseSession, PersistedSession

    suspicious = ("api_key", "apikey", "mimo", "audio", "secret", "credential", "token")
    offending = []
    for model in (PersistedSession, DefenseSession, ConversationTurn):
        for field_name in model.model_fields:
            lowered = field_name.lower()
            if any(s in lowered for s in suspicious):
                offending.append(f"{model.__name__}.{field_name}")
    assert offending == [], f"credential/audio-shaped field(s) on the persisted schema: {offending}"


def test_committed_example_session_carries_no_credential_shaped_key():
    """The mirror of `scripts/inventory_committed_document_text.py`'s
    `_assert_no_credential_shaped_keys`, run directly against the actual
    committed fixture rather than the Pydantic type — catches a leak in the raw
    JSON even if a future schema change made `PersistedSession` silently ignore
    an unknown field instead of rejecting it."""
    import json

    from academic_defense_simulator.example_session import EXAMPLE_SESSION_PATH

    suspicious = ("api_key", "apikey", "mimo", "secret", "credential", "token")

    def walk(node: object) -> list[str]:
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if any(s in key.lower() for s in suspicious) and value:
                    found.append(key)
                found.extend(walk(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(walk(item))
        return found

    raw = json.loads(EXAMPLE_SESSION_PATH.read_text(encoding="utf-8"))
    offending = walk(raw)
    assert offending == [], f"credential-shaped key(s) in the committed example session: {offending}"
