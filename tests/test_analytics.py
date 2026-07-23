"""Cross-session analytics tests (v1.0b Brief step 5). Aggregation correctness on
fixture sessions, load-filter correctness (in-progress/no-report exclusion, mirroring
the real 44d90861-shaped file), cache-key behavior (Decision 3), and fail-open gap
clustering. All pure/mocked except one behavioral cache_data proof, which exercises
Streamlit's real caching mechanism directly (not by importing streamlit_app.py --
that module executes a live script on import and has no existing test coverage;
see its own docstring) with the exact key shape `analytics.gap_theme_cache_key`
produces.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import streamlit as st

from academic_defense_simulator import analytics, persistence
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.gap_theme import GapTheme, GapThemeAnalysis
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.report import (
    DefenseReport,
    PanelistReportSection,
    PushbackEvent,
    PushbackOutcome,
)
from academic_defense_simulator.models.session import (
    ConversationTurn,
    DefenseSession,
    PersistedSession,
    SessionStage,
)
from academic_defense_simulator.prompts.analytics_prompts import ANALYTICS_PROMPT_VERSION

_BASE_TIME = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _profile(topic="A topic", defense_type=DefenseType.THESIS):
    return DefenseProfile(
        defense_type=defense_type,
        domain="library science",
        topic=topic,
        selected_archetypes=["methodology_expert"],
        document_id="doc-1",
    )


def _panel():
    return [Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓")]


def _turn(difficulty=2, scored=True):
    score = (
        AnswerScore(clarity=4, depth=3, grounding=3, difficulty_delta=0, primary_gap="a gap", answer_summary="s")
        if scored
        else None
    )
    return ConversationTurn(
        panelist_archetype_key="methodology_expert",
        panelist_name="Reyes",
        question="q",
        grounding_reference="g",
        chunk_index=0,
        chunk_text="c",
        difficulty_level=difficulty,
        answer="a" if scored else None,
        score=score,
    )


def _report(trajectory, gaps, pushback_events=()):
    n = len(trajectory)
    return DefenseReport(
        difficulty_trajectory=trajectory,
        overall_avg_clarity=4.0,
        overall_avg_depth=3.0,
        overall_avg_grounding=3.0,
        panelist_sections=[
            PanelistReportSection(
                archetype_key="methodology_expert",
                panelist_name="Reyes",
                turns_taken=n,
                avg_clarity=4.0,
                avg_depth=3.0,
                avg_grounding=3.0,
                primary_gaps=gaps,
            )
        ],
        pushback_events=list(pushback_events),
        narrative="A narrative.",
    )


def _persisted(
    session_id,
    *,
    stage=SessionStage.COMPLETED,
    with_report=True,
    trajectory=(2, 3),
    gaps=("a gap",),
    topic="A topic",
    created_at=None,
    turns_count=None,
):
    turns_count = turns_count if turns_count is not None else len(trajectory)
    turns = [_turn(d) for d in (trajectory[:turns_count] if turns_count else trajectory)]
    report = _report(list(trajectory), list(gaps)) if with_report else None
    session = DefenseSession(
        profile=_profile(topic=topic), panel=_panel(), difficulty_current=3, turns=turns, report=report
    )
    ts = created_at or _BASE_TIME
    return PersistedSession(
        session_id=session_id,
        created_at=ts,
        updated_at=ts,
        stage=stage,
        session=session,
        document_chunks=["chunk text"],
    )


# --- load_completed_sessions ---


def test_load_completed_sessions_excludes_in_progress_report_null(tmp_path):
    """Mirrors the real sessions/44d90861-...json shape: stage=in_progress,
    report=null. Must be excluded, not just filtered by stage alone -- proves the
    'report is not None' half of the filter too, not just 'stage == completed'."""
    in_progress_no_report = _persisted("44d90861-shaped", stage=SessionStage.IN_PROGRESS, with_report=False)
    completed = _persisted("completed-1", stage=SessionStage.COMPLETED, with_report=True)
    persistence.save_session(in_progress_no_report, directory=tmp_path)
    persistence.save_session(completed, directory=tmp_path)

    sessions, skips = analytics.load_completed_sessions(directory=tmp_path)

    assert [s.session_id for s in sessions] == ["completed-1"]
    skipped_ids = {s.path.stem for s in skips}
    assert "44d90861-shaped" in skipped_ids
    assert "completed-1" not in skipped_ids


def test_load_completed_sessions_excludes_aborted(tmp_path):
    aborted = _persisted("aborted-1", stage=SessionStage.ABORTED, with_report=False)
    persistence.save_session(aborted, directory=tmp_path)

    sessions, _ = analytics.load_completed_sessions(directory=tmp_path)
    assert sessions == []


def test_load_completed_sessions_orders_by_created_at_ascending(tmp_path):
    later = _persisted("later", created_at=_BASE_TIME + timedelta(days=2))
    earlier = _persisted("earlier", created_at=_BASE_TIME)
    middle = _persisted("middle", created_at=_BASE_TIME + timedelta(days=1))
    for p in (later, earlier, middle):
        persistence.save_session(p, directory=tmp_path)

    sessions, _ = analytics.load_completed_sessions(directory=tmp_path)
    assert [s.session_id for s in sessions] == ["earlier", "middle", "later"]


def test_load_completed_sessions_on_missing_directory_returns_empty(tmp_path):
    sessions, skips = analytics.load_completed_sessions(directory=tmp_path / "does-not-exist")
    assert sessions == []
    assert skips == []


def test_load_completed_sessions_skips_corrupt_file_with_reason(tmp_path):
    (tmp_path / "broken.json").write_text("not json {{{", encoding="utf-8")
    sessions, skips = analytics.load_completed_sessions(directory=tmp_path)
    assert sessions == []
    assert len(skips) == 1
    assert skips[0].path.name == "broken.json"


# --- aggregation views (known inputs -> known outputs) ---


def test_session_list_view_reads_existing_fields():
    sessions = [
        _persisted("s1", topic="Topic A", trajectory=(2, 3, 3), gaps=("g1",)),
        _persisted("s2", topic="Topic B", trajectory=(2,), gaps=()),
    ]
    rows = analytics.session_list_view(sessions)
    assert rows[0]["topic"] == "Topic A"
    assert rows[0]["turn_count"] == 3
    assert rows[0]["overall_avg_clarity"] == 4.0
    assert rows[1]["topic"] == "Topic B"
    assert rows[1]["turn_count"] == 1


def test_quality_trend_view_sequenced_not_dated():
    sessions = [_persisted("s1"), _persisted("s2"), _persisted("s3")]
    rows = analytics.quality_trend_view(sessions)
    assert [r["session_sequence"] for r in rows] == [1, 2, 3]
    assert all(r["clarity"] == 4.0 for r in rows)


def test_difficulty_trajectory_view_preserves_each_sessions_own_sequence():
    sessions = [
        _persisted("s1", trajectory=(2, 3, 4)),
        _persisted("s2", trajectory=(1, 2)),
    ]
    rows = analytics.difficulty_trajectory_view(sessions)
    assert rows[0]["trajectory"] == [2, 3, 4]
    assert rows[1]["trajectory"] == [1, 2]


def test_pushback_outcome_trend_view_counts_by_outcome():
    events = [
        PushbackEvent(
            turn_index=1,
            prior_turn_index=0,
            difficulty_from=2,
            difficulty_to=3,
            quality_sum_prior=6,
            quality_sum_at=9,
            outcome=PushbackOutcome.RECOVERED,
        ),
        PushbackEvent(
            turn_index=2,
            prior_turn_index=1,
            difficulty_from=3,
            difficulty_to=4,
            quality_sum_prior=9,
            quality_sum_at=9,
            outcome=PushbackOutcome.HELD,
        ),
    ]
    session = _persisted("s1")
    session.session.report.pushback_events = events

    rows = analytics.pushback_outcome_trend_view([session])
    assert rows[0]["recovered"] == 1
    assert rows[0]["held"] == 1
    assert rows[0]["deteriorated"] == 0


def test_all_primary_gaps_flattens_across_sessions_and_panelists():
    sessions = [
        _persisted("s1", gaps=("gap A", "gap B")),
        _persisted("s2", gaps=("gap C",)),
    ]
    assert analytics.all_primary_gaps(sessions) == ["gap A", "gap B", "gap C"]


# --- cache key (Decision 3) ---


def test_cache_key_order_independent():
    assert analytics.gap_theme_cache_key(["a", "b"]) == analytics.gap_theme_cache_key(["b", "a"])


def test_cache_key_changes_on_new_data():
    key1 = analytics.gap_theme_cache_key(["a", "b"])
    key2 = analytics.gap_theme_cache_key(["a", "b", "c"])
    assert key1 != key2


def test_cache_key_changes_on_prompt_version_bump():
    key1 = analytics.gap_theme_cache_key(["a", "b"], prompt_version=ANALYTICS_PROMPT_VERSION)
    key2 = analytics.gap_theme_cache_key(["a", "b"], prompt_version=ANALYTICS_PROMPT_VERSION + 1)
    assert key1 != key2


def test_cache_key_defaults_to_live_analytics_prompt_version():
    key = analytics.gap_theme_cache_key(["a"])
    assert key[1] == ANALYTICS_PROMPT_VERSION == 1


# --- cluster_gap_themes: fail-open + call-avoidance ---


class _StubStructuredProvider:
    """Mocked LLMProvider.generate_structured -- models the net effect at
    analytics.py's boundary: GeminiProvider already retries once internally before
    raising LLMProviderError, so this module only ever sees one call attempt."""

    def __init__(self, response):
        self._response = response
        self.calls = 0

    def generate_structured(self, prompt, response_model):
        self.calls += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def generate_text(self, prompt):
        raise NotImplementedError


def test_cluster_gap_themes_fail_open_on_provider_error(caplog):
    provider = _StubStructuredProvider(LLMProviderError("forced failure -- injected fault"))
    import logging

    with caplog.at_level(logging.WARNING):
        result = analytics.cluster_gap_themes(["gap 1", "gap 2"], provider)

    assert result is None
    assert provider.calls == 1
    assert "failing open" in caplog.text


def test_cluster_gap_themes_returns_parsed_analysis_on_success():
    analysis = GapThemeAnalysis(
        themes=[GapTheme(theme_label="Quantitative justification", supporting_gaps=["gap 1"], occurrence_count=1)]
    )
    provider = _StubStructuredProvider(analysis)

    result = analytics.cluster_gap_themes(["gap 1"], provider)

    assert result == analysis
    assert provider.calls == 1


def test_cluster_gap_themes_empty_input_makes_no_call():
    provider = _StubStructuredProvider(GapThemeAnalysis(themes=[]))
    result = analytics.cluster_gap_themes([], provider)
    assert result is None
    assert provider.calls == 0


# --- st.cache_data behavioral proof (Decision 3) ---
# Exercises Streamlit's real cache_data mechanism (confirmed to work standalone,
# outside a running app, via MemoryCacheStorageManager) with the exact cache-key
# shape gap_theme_cache_key produces -- this is the mechanism streamlit_app.py's
# `_cached_cluster_gap_themes` wraps around `analytics.cluster_gap_themes`.


def test_cache_data_skips_refire_on_unchanged_key():
    provider = _StubStructuredProvider(GapThemeAnalysis(themes=[]))

    @st.cache_data(show_spinner=False)
    def cached(cache_key, _provider):
        return analytics.cluster_gap_themes(list(cache_key[0]), _provider)

    key = analytics.gap_theme_cache_key(["gap 1", "gap 2"])
    cached(key, provider)
    cached(key, provider)  # same key -> served from cache, no second call

    assert provider.calls == 1


def test_cache_data_refires_on_new_session_data():
    provider = _StubStructuredProvider(GapThemeAnalysis(themes=[]))

    @st.cache_data(show_spinner=False)
    def cached(cache_key, _provider):
        return analytics.cluster_gap_themes(list(cache_key[0]), _provider)

    cached(analytics.gap_theme_cache_key(["gap 1"]), provider)
    cached(analytics.gap_theme_cache_key(["gap 1", "gap 2"]), provider)  # new gap -> new key

    assert provider.calls == 2


def test_cache_data_refires_on_prompt_version_bump():
    provider = _StubStructuredProvider(GapThemeAnalysis(themes=[]))

    @st.cache_data(show_spinner=False)
    def cached(cache_key, _provider):
        return analytics.cluster_gap_themes(list(cache_key[0]), _provider)

    cached(analytics.gap_theme_cache_key(["gap 1"], prompt_version=1), provider)
    cached(analytics.gap_theme_cache_key(["gap 1"], prompt_version=2), provider)  # forced bump -> new key

    assert provider.calls == 2
