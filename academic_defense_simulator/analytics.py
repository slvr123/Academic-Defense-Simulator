"""Cross-session analytics (v1.0b) — business logic only, no Streamlit imports
(import-boundary rule; verified by `git grep` per the brief). Reads every
`sessions/*.json`, aggregates pure Python over fields `DefenseReport` already
computes. The one exception is `cluster_gap_themes`, a single cached, on-demand
`gemini-2.5-flash` call — see `docs/v1.0b-analytics-decisions.md` Decision 2.

Kept architecturally separate from `report.py`'s v1.0b-2 answer-suggestions work:
different data flow (many sessions in, not one), different prompt module, different
version constant (`ANALYTICS_PROMPT_VERSION`, never `ANSWER_SUGGESTIONS_PROMPT_VERSION`
or `PROMPT_VERSION`), different fail-open path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from academic_defense_simulator import persistence
from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError
from academic_defense_simulator.models.gap_theme import GapThemeAnalysis
from academic_defense_simulator.models.session import PersistedSession, SessionStage
from academic_defense_simulator.prompts.analytics_prompts import (
    ANALYTICS_PROMPT_VERSION,
    GAP_CLUSTERING_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = persistence.DEFAULT_SESSIONS_DIR


@dataclass(frozen=True)
class SessionSkip:
    """A file in the sessions directory that could not be loaded, and why. Mirrors
    `persistence.SessionSkip`'s shape (surfaced as data, never raised) — not reused
    directly since this module builds its own directory scan (see
    `load_completed_sessions`); `persistence.list_sessions` filters to in-progress
    sessions only and has no completed-sessions equivalent to call into."""

    path: Path
    reason: str


def load_completed_sessions(
    directory: Path = DEFAULT_SESSIONS_DIR,
) -> tuple[list[PersistedSession], list[SessionSkip]]:
    """Every completed session with a report, ordered by `created_at` ascending
    (Decision 1). Sessions with no report (killed/aborted runs, or completed-but-
    somehow-report-less) are excluded — they carry nothing to aggregate. Reads each
    file through the existing `persistence.load_session`, one file at a time (an
    absent directory is zero sessions, not an error, same as `persistence.list_sessions`).
    """
    if not directory.is_dir():
        return [], []

    sessions: list[PersistedSession] = []
    skips: list[SessionSkip] = []
    for path in sorted(directory.glob("*.json")):
        session_id = path.stem
        try:
            persisted = persistence.load_session(session_id, directory)
        except (OSError, ValidationError) as exc:
            skips.append(SessionSkip(path=path, reason=f"Couldn't load session: {exc}"))
            continue

        if persisted.stage == SessionStage.COMPLETED and persisted.session.report is not None:
            sessions.append(persisted)
        else:
            skips.append(
                SessionSkip(
                    path=path,
                    reason=f"stage={persisted.stage.value!r}, report present={persisted.session.report is not None}",
                )
            )

    sessions.sort(key=lambda p: p.created_at)
    return sessions, skips


def session_list_view(sessions: list[PersistedSession]) -> list[dict]:
    """Decision 1, view 1 — one row per completed session: date, defense_type,
    topic, overall averages, turn count. A practice log, nothing computed beyond
    reading existing fields."""
    rows = []
    for persisted in sessions:
        session = persisted.session
        report = session.report
        assert report is not None  # guaranteed by load_completed_sessions' filter
        rows.append(
            {
                "date": persisted.created_at,
                "defense_type": session.profile.defense_type.value,
                "topic": session.profile.topic,
                "overall_avg_clarity": report.overall_avg_clarity,
                "overall_avg_depth": report.overall_avg_depth,
                "overall_avg_grounding": report.overall_avg_grounding,
                "turn_count": len(session.turns),
            }
        )
    return rows


def quality_trend_view(sessions: list[PersistedSession]) -> list[dict]:
    """Decision 1, view 2 — overall_avg_clarity/depth/grounding per session, in
    session-sequence order (x-axis = sequence, not date, so unevenly-spaced
    sessions don't visually compress/stretch the trend)."""
    rows = []
    for i, persisted in enumerate(sessions, start=1):
        report = persisted.session.report
        assert report is not None
        rows.append(
            {
                "session_sequence": i,
                "clarity": report.overall_avg_clarity,
                "depth": report.overall_avg_depth,
                "grounding": report.overall_avg_grounding,
            }
        )
    return rows


def difficulty_trajectory_view(sessions: list[PersistedSession]) -> list[dict]:
    """Decision 1, view 3 — each session's difficulty_trajectory as its own labeled
    sequence (not one merged chart — sessions have different turn counts, so an
    overlaid x-axis would compare incompatible things)."""
    rows = []
    for i, persisted in enumerate(sessions, start=1):
        report = persisted.session.report
        assert report is not None
        rows.append(
            {
                "session_sequence": i,
                "topic": persisted.session.profile.topic,
                "date": persisted.created_at,
                "trajectory": list(report.difficulty_trajectory),
            }
        )
    return rows


def pushback_outcome_trend_view(sessions: list[PersistedSession]) -> list[dict]:
    """Decision 1, view 4 — recovered/held/deteriorated counts per session, in
    session-sequence order."""
    rows = []
    for i, persisted in enumerate(sessions, start=1):
        report = persisted.session.report
        assert report is not None
        recovered = sum(1 for e in report.pushback_events if e.outcome.value == "recovered")
        held = sum(1 for e in report.pushback_events if e.outcome.value == "held")
        deteriorated = sum(1 for e in report.pushback_events if e.outcome.value == "deteriorated")
        rows.append(
            {
                "session_sequence": i,
                "recovered": recovered,
                "held": held,
                "deteriorated": deteriorated,
            }
        )
    return rows


def all_primary_gaps(sessions: list[PersistedSession]) -> list[str]:
    """Flat list of every non-null primary_gap across every completed session's
    every panelist section — the input to `cluster_gap_themes` (Decision 2:
    recurrence is only meaningful across the whole practice history, not per
    session or per panelist)."""
    gaps: list[str] = []
    for persisted in sessions:
        report = persisted.session.report
        assert report is not None
        for section in report.panelist_sections:
            gaps.extend(section.primary_gaps)
    return gaps


def gap_theme_cache_key(
    primary_gaps: list[str], prompt_version: int = ANALYTICS_PROMPT_VERSION
) -> tuple[tuple[str, ...], int]:
    """Decision 3's cache key: a hash of (sorted primary_gap strings,
    ANALYTICS_PROMPT_VERSION). Returned as a plain hashable tuple rather than an
    actual hash digest — Streamlit's `st.cache_data` hashes its arguments itself;
    this just gives the caller (streamlit_app.py) a deterministic, order-independent
    key to pass in. `prompt_version` defaults to the live constant but is accepted
    as a parameter so a version bump can be forced/tested without editing the
    prompts module."""
    return (tuple(sorted(primary_gaps)), prompt_version)


def cluster_gap_themes(primary_gaps: list[str], provider: LLMProvider) -> Optional[GapThemeAnalysis]:
    """One `gemini-2.5-flash` call (Decision 2) — the caller is responsible for
    pinning the provider to that model, same convention as
    `document_relevance.assess_document`. Fail-open: on any API error, returns
    None; the caller renders 'Theme analysis unavailable this session' rather than
    crashing or blocking the four free views. Returns None with no call at all if
    there are no gaps to cluster (nothing to ask about)."""
    if not primary_gaps:
        return None

    gaps_list = "\n".join(f"- {gap}" for gap in primary_gaps)
    prompt = GAP_CLUSTERING_SYSTEM_PROMPT.format(gaps_list=gaps_list)

    try:
        return provider.generate_structured(prompt, GapThemeAnalysis)
    except LLMProviderError as exc:
        logger.warning("Gap theme clustering failed, failing open: %s", exc)
        return None
