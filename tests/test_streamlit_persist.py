"""`_persist` failure-path tests (v0.4b amendment: dev-hot-reload session-identity
mismatch). `streamlit_app.py` has no prior test coverage -- it imports cleanly
outside a running app ("bare mode": `st.session_state` degrades to a working
in-memory dict-like with a benign warning, confirmed empirically), so `_persist`
is called directly here rather than mocked at a distance.

Two failure classes, two tests, run side by side (Step 2.5): disk I/O (OSError,
forced via monkeypatching `persistence.save_session` to raise directly -- Windows
doesn't give a reliable, portable way to make a directory genuinely unwritable,
same reasoning the project already applies elsewhere for forcing provider
failures) and the stale-class ValidationError (forced via `importlib.reload` --
see `_reloaded_session_module` below for why that specific mechanism, and not an
unregistered second module object, is what's needed to actually reproduce the
fault). Both leave `st.session_state.save_failed` True but must produce
distinguishable log lines and captions.

Reload-isolation note: `importlib.reload` mutates the real, shared
`sys.modules['academic_defense_simulator.models.session']` entry in place. This
is safe here because pytest fully imports every test module (binding each
file's own `from ... import DefenseSession`-style names) during collection,
*before* any test function body runs -- so a reload triggered from inside a
test body cannot retroactively change names other test files already captured.
No other test in this suite does a deferred/local import of this module (only
top-level imports, confirmed by inspection), so nothing downstream re-fetches
the reloaded class. The module is reloaded again after each use as a courtesy
so it's left in a fresh, internally-consistent state either way.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone

import academic_defense_simulator.models.session as real_session_module
import academic_defense_simulator.streamlit_app as app
from academic_defense_simulator import persistence
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.session import DefenseSession as RealDefenseSession
from academic_defense_simulator.rag.retrieval import Chunk


def _profile():
    return DefenseProfile(
        defense_type=DefenseType.THESIS,
        domain="library science",
        topic="t",
        selected_archetypes=["methodology_expert"],
        document_id="doc-1",
    )


def _panel():
    return [Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓")]


def _real_session_instance() -> RealDefenseSession:
    return RealDefenseSession(profile=_profile(), panel=_panel(), difficulty_current=2)


def _reloaded_session_module():
    """`importlib.reload(real_session_module)` -- this is the exact mechanism
    Streamlit's local_sources_watcher uses when it detects a changed source
    file mid-session and reruns: it reloads the changed module IN PLACE (same
    `sys.modules` entry, same module object, freshly re-executed), producing a
    new `DefenseSession`/`PersistedSession` class with an identical
    __module__/__qualname__ but a new id().

    Empirically confirmed this is the specific mechanism required: loading a
    structurally-identical second module via `importlib.util.spec_from_file_location`
    WITHOUT registering it in `sys.modules` does NOT reproduce the ValidationError
    (verified directly -- construction succeeds cleanly against the unregistered
    module's PersistedSession, unlike this reload path). Only the in-place reload,
    which pydantic-core's schema-ref caching treats as genuinely invalidating the
    prior class, reproduces the real bug."""
    return importlib.reload(real_session_module)


def _set_up_session_state(monkeypatch, session_instance) -> None:
    monkeypatch.setattr(app, "_persistence_enabled", lambda: True)
    app.st.session_state.session_id = "test-session-id"
    app.st.session_state.session_created_at = datetime.now(timezone.utc)
    app.st.session_state.session = session_instance
    app.st.session_state.chunks = [Chunk(text="excerpt", embedding=[0.1, 0.2])]
    app.st.session_state.save_failed = False
    app.st.session_state.save_failed_reason = None


# --- OSError path (disk I/O) ---


def test_persist_oserror_logs_and_sets_disk_reason(monkeypatch, caplog):
    session_instance = _real_session_instance()
    _set_up_session_state(monkeypatch, session_instance)

    def _raise_oserror(persisted):
        raise OSError("Disk full (simulated)")

    monkeypatch.setattr(persistence, "save_session", _raise_oserror)

    with caplog.at_level(logging.WARNING):
        app._persist(app.SessionStage.IN_PROGRESS)

    assert app.st.session_state.save_failed is True
    assert app.st.session_state.save_failed_reason == "disk"
    assert "session save failed (session_id=test-session-id): Disk full (simulated)" in caplog.text
    # Session object itself is untouched -- _persist never mutates it.
    assert app.st.session_state.session is session_instance


# --- ValidationError path (stale class from dev hot-reload) ---


def test_persist_stale_class_validation_error_logs_and_sets_distinct_reason(monkeypatch, caplog):
    session_instance = _real_session_instance()

    try:
        reloaded = _reloaded_session_module()
        SecondDefenseSession = reloaded.DefenseSession
        SecondPersistedSession = reloaded.PersistedSession

        # This assertion IS the proof the simulated fault matches the real bug's
        # signature, not just "some ValidationError."
        assert type(session_instance).__module__ == SecondDefenseSession.__module__
        assert type(session_instance).__qualname__ == SecondDefenseSession.__qualname__
        assert type(session_instance) is not SecondDefenseSession

        _set_up_session_state(monkeypatch, session_instance)
        # `_build_persisted_session` resolves the bare name `PersistedSession` from
        # streamlit_app's own module globals at call time -- swapping it here is
        # exactly what a mid-session module reload does to that same lookup.
        monkeypatch.setattr(app, "PersistedSession", SecondPersistedSession)

        with caplog.at_level(logging.WARNING):
            app._persist(app.SessionStage.IN_PROGRESS)

        assert app.st.session_state.save_failed is True
        assert app.st.session_state.save_failed_reason == "stale_class"
        assert "Save skipped: stale class reference detected (likely mid-session code reload)." in caplog.text
        assert "validation error for PersistedSession" in caplog.text
        assert "Input should be a valid dictionary or instance of DefenseSession" in caplog.text
        # Session object itself is untouched -- _persist never mutates it, even on
        # a forced validation failure while building the persisted copy.
        assert app.st.session_state.session is session_instance
    finally:
        # Leaves the shared module in a fresh, internally-consistent state
        # regardless of outcome -- see the reload-isolation note at module top.
        importlib.reload(real_session_module)


def test_persist_success_clears_reason(monkeypatch):
    """Sanity check the two failure tests above against the happy path: a
    successful save clears both save_failed and save_failed_reason."""
    session_instance = _real_session_instance()
    _set_up_session_state(monkeypatch, session_instance)
    app.st.session_state.save_failed = True
    app.st.session_state.save_failed_reason = "stale_class"

    monkeypatch.setattr(persistence, "save_session", lambda persisted: None)

    app._persist(app.SessionStage.IN_PROGRESS)

    assert app.st.session_state.save_failed is False
    assert app.st.session_state.save_failed_reason is None
