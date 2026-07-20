"""Session persistence tests (v0.4b Brief step 7): round-trip serialization, atomic
writes, corrupt-file/version-mismatch skip handling, in-progress filtering, delete.
All pure filesystem logic (tmp_path fixtures) — zero LLM calls, zero Streamlit.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from academic_defense_simulator import persistence
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import (
    DefenseReport,
    PanelistReportSection,
    PushbackEvent,
    PushbackOutcome,
)
from academic_defense_simulator.models.session import (
    ConversationTurn,
    CURRENT_SCHEMA_VERSION,
    DefenseSession,
    PersistedSession,
    SessionStage,
)
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY


def _profile(**overrides):
    fields = dict(
        defense_type=DefenseType.THESIS,
        domain="library science",
        topic="Adaptive retrieval for thesis defense",
        selected_archetypes=["methodology_expert"],
        document_id="doc-1",
    )
    fields.update(overrides)
    return DefenseProfile(**fields)


def _panel():
    return [
        Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓"),
        Panelist(archetype_key=DEVILS_ADVOCATE_KEY, panelist_name="Marlowe", persona_framing="f", icon="⚔️"),
    ]


def _turn(**overrides):
    fields = dict(
        panelist_archetype_key="methodology_expert",
        panelist_name="Reyes",
        question="Why this method?",
        grounding_reference="the chosen approach",
        chunk_index=0,
        chunk_text="Excerpt discussing the chosen approach.",
        difficulty_level=2,
        answer="Because it fits the data.",
        score=AnswerScore(
            clarity=4, depth=3, grounding=3, difficulty_delta=1, primary_gap="depth", answer_summary="claim"
        ),
    )
    fields.update(overrides)
    return ConversationTurn(**fields)


def _report():
    return DefenseReport(
        difficulty_trajectory=[2, 3],
        overall_avg_clarity=4.0,
        overall_avg_depth=3.0,
        overall_avg_grounding=3.0,
        panelist_sections=[
            PanelistReportSection(
                archetype_key="methodology_expert",
                panelist_name="Reyes",
                turns_taken=1,
                avg_clarity=4.0,
                avg_depth=3.0,
                avg_grounding=3.0,
                primary_gaps=["depth"],
            )
        ],
        pushback_events=[
            PushbackEvent(
                turn_index=1,
                prior_turn_index=0,
                difficulty_from=2,
                difficulty_to=3,
                quality_sum_prior=10,
                quality_sum_at=13,
                outcome=PushbackOutcome.RECOVERED,
            )
        ],
        narrative="A solid defense with room to deepen methodology discussion.",
    )


def _persisted_session(*, stage=SessionStage.IN_PROGRESS, with_report=False, session_id="sess-1"):
    session = DefenseSession(
        profile=_profile(),
        panel=_panel(),
        difficulty_current=3,
        turns=[_turn()],
        report=_report() if with_report else None,
    )
    now = datetime.now(timezone.utc)
    return PersistedSession(
        session_id=session_id,
        created_at=now,
        updated_at=now,
        stage=stage,
        session=session,
        document_chunks=["Excerpt discussing the chosen approach.", "A second chunk of document text."],
    )


# --- Round-trip serialization (Brief step 1) ---


def test_persisted_session_round_trips_losslessly():
    persisted = _persisted_session(with_report=True)
    restored = PersistedSession.model_validate_json(persisted.model_dump_json())
    assert restored == persisted


def test_every_constituent_model_round_trips():
    """Each model PersistedSession is built from, round-tripped independently —
    the brief's 'confirm every constituent model round-trips' item."""
    profile = _profile()
    assert DefenseProfile.model_validate_json(profile.model_dump_json()) == profile

    panel = _panel()
    for panelist in panel:
        assert Panelist.model_validate_json(panelist.model_dump_json()) == panelist

    turn = _turn()
    assert ConversationTurn.model_validate_json(turn.model_dump_json()) == turn

    report = _report()
    assert DefenseReport.model_validate_json(report.model_dump_json()) == report

    session = DefenseSession(profile=profile, panel=panel, difficulty_current=2, turns=[turn], report=report)
    assert DefenseSession.model_validate_json(session.model_dump_json()) == session


def test_schema_version_defaults_to_current():
    persisted = _persisted_session()
    assert persisted.schema_version == CURRENT_SCHEMA_VERSION == 1


# --- save_session / load_session (atomic write) ---


def test_save_then_load_round_trips(tmp_path):
    persisted = _persisted_session()
    persistence.save_session(persisted, directory=tmp_path)
    loaded = persistence.load_session(persisted.session_id, directory=tmp_path)
    assert loaded == persisted


def test_save_writes_no_leftover_tmp_file(tmp_path):
    persisted = _persisted_session()
    persistence.save_session(persisted, directory=tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / f"{persisted.session_id}.json").exists()


def test_save_creates_directory_if_missing(tmp_path):
    directory = tmp_path / "sessions"
    assert not directory.exists()
    persistence.save_session(_persisted_session(), directory=directory)
    assert directory.is_dir()


def test_resave_overwrites_via_atomic_replace(tmp_path):
    persisted = _persisted_session(stage=SessionStage.IN_PROGRESS)
    persistence.save_session(persisted, directory=tmp_path)

    updated = persisted.model_copy(update={"stage": SessionStage.COMPLETED})
    persistence.save_session(updated, directory=tmp_path)

    loaded = persistence.load_session(persisted.session_id, directory=tmp_path)
    assert loaded.stage == SessionStage.COMPLETED
    # Exactly one real file on disk for this session_id — no stray tmp/duplicate.
    assert list(tmp_path.glob(f"{persisted.session_id}*")) == [tmp_path / f"{persisted.session_id}.json"]


# --- list_sessions: in-progress filtering ---


def test_list_sessions_returns_only_in_progress(tmp_path):
    in_progress = _persisted_session(session_id="in-progress-1", stage=SessionStage.IN_PROGRESS)
    completed = _persisted_session(session_id="completed-1", stage=SessionStage.COMPLETED, with_report=True)
    aborted = _persisted_session(session_id="aborted-1", stage=SessionStage.ABORTED)
    for persisted in (in_progress, completed, aborted):
        persistence.save_session(persisted, directory=tmp_path)

    sessions, skips = persistence.list_sessions(directory=tmp_path)
    assert skips == []
    assert {s.session_id for s in sessions} == {"in-progress-1"}


def test_list_sessions_on_missing_directory_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist"
    sessions, skips = persistence.list_sessions(directory=missing)
    assert sessions == []
    assert skips == []


def test_list_sessions_on_empty_directory_returns_empty(tmp_path):
    sessions, skips = persistence.list_sessions(directory=tmp_path)
    assert sessions == []
    assert skips == []


# --- corrupt-file / version-mismatch skip handling (Decision 3, Decision 7) ---


def test_corrupt_json_file_is_skipped_with_reason(tmp_path):
    bad_path = tmp_path / "broken.json"
    bad_path.write_text("this is not valid json {{{", encoding="utf-8")

    sessions, skips = persistence.list_sessions(directory=tmp_path)
    assert sessions == []
    assert len(skips) == 1
    assert skips[0].path == bad_path
    assert "Corrupt" in skips[0].reason


def test_structurally_malformed_but_valid_json_is_skipped(tmp_path):
    """Valid JSON, current schema_version, but missing required fields — a real
    file corrupted mid-write is more likely to look like this than like raw
    invalid JSON, so this must be caught too, not just the JSONDecodeError case."""
    bad_path = tmp_path / "malformed.json"
    bad_path.write_text(json.dumps({"schema_version": CURRENT_SCHEMA_VERSION, "session_id": "x"}), encoding="utf-8")

    sessions, skips = persistence.list_sessions(directory=tmp_path)
    assert sessions == []
    assert len(skips) == 1
    assert skips[0].path == bad_path


def test_incompatible_schema_version_is_skipped_with_named_reason(tmp_path):
    persisted = _persisted_session(session_id="future-version")
    payload = json.loads(persisted.model_dump_json())
    payload["schema_version"] = 999
    (tmp_path / "future-version.json").write_text(json.dumps(payload), encoding="utf-8")

    sessions, skips = persistence.list_sessions(directory=tmp_path)
    assert sessions == []
    assert len(skips) == 1
    assert "incompatible version" in skips[0].reason
    assert "999" in skips[0].reason


def test_one_corrupt_file_does_not_block_valid_sessions(tmp_path):
    good = _persisted_session(session_id="good-1", stage=SessionStage.IN_PROGRESS)
    persistence.save_session(good, directory=tmp_path)
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")

    sessions, skips = persistence.list_sessions(directory=tmp_path)
    assert {s.session_id for s in sessions} == {"good-1"}
    assert len(skips) == 1


def test_loading_incompatible_version_directly_raises():
    """`load_session` (used by the resume button on an already-filtered id) is not
    expected to hit this path in practice, but it must fail loud rather than
    half-load — same contract PersistedSession's own validation gives it."""
    with pytest.raises(ValidationError):
        PersistedSession.model_validate_json(json.dumps({"schema_version": 2}))


# --- delete_session ---


def test_delete_session_removes_file(tmp_path):
    persisted = _persisted_session()
    persistence.save_session(persisted, directory=tmp_path)
    persistence.delete_session(persisted.session_id, directory=tmp_path)
    assert not (tmp_path / f"{persisted.session_id}.json").exists()


def test_delete_nonexistent_session_does_not_raise(tmp_path):
    persistence.delete_session("never-existed", directory=tmp_path)  # must not raise
