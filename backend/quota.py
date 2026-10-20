"""The daily Gemini quota guard: how many defenses a day the free tier can carry,
and a durable count of how many have started.

The cap is per Pacific day because Gemini's requests-per-day reset at midnight
Pacific. Every model a session uses has its own RPD, so the cap is the smallest
of floor(RPD / that model's worst-case calls per session) across the models, less
a margin. Counting sessions is then enough: no model can run out before the cap.

The count lives in Postgres (Neon) so a restart or a redeploy doesn't hand out a
fresh day's worth. A reservation is one UPDATE that only succeeds while the count
is under the cap, so two defenses starting at once can't both take the last slot.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as day_start
from typing import Protocol
from zoneinfo import ZoneInfo

import psycopg
from annotated_types import MaxLen
from psycopg import sql
from psycopg_pool import ConnectionPool

from academic_defense_simulator.call_budget import CallRole, calls_per_model, worst_case_calls
from academic_defense_simulator.models.defense_profile import DefenseProfile
from backend.settings import Settings

logger = logging.getLogger("backend.quota")

PACIFIC = ZoneInfo("America/Los_Angeles")
LOW_CAP_WARNING = 3

# v1's intake allows this many domain panelists; Devil's Advocate always joins them.
MAX_DOMAIN_PANELISTS = next(
    m.max_length for m in DefenseProfile.model_fields["selected_archetypes"].metadata if isinstance(m, MaxLen)
)
MAX_PANEL_SIZE = MAX_DOMAIN_PANELISTS + 1


def pacific_day(now: datetime) -> date:
    """The quota day `now` (timezone-aware) falls in."""
    return now.astimezone(PACIFIC).date()


def resets_at(day: date) -> datetime:
    """Midnight Pacific at the end of `day`, with that night's UTC offset (DST-aware)."""
    return datetime.combine(day + timedelta(days=1), day_start(0), tzinfo=PACIFIC)


@dataclass(frozen=True)
class ModelShare:
    model: str
    calls_per_session: int
    rpd: int

    @property
    def sessions(self) -> int:
        return self.rpd // self.calls_per_session


@dataclass(frozen=True)
class QuotaPlan:
    shares: tuple[ModelShare, ...]
    margin: int

    @property
    def binding(self) -> ModelShare:
        """The model whose RPD runs out first, which sets the cap."""
        return min(self.shares, key=lambda share: share.sessions)

    @property
    def cap(self) -> int:
        return max(0, self.binding.sessions - self.margin)


def role_models(settings: Settings) -> dict[CallRole, str]:
    """Which model serves each call role in the backend. Everything runs on the
    defense model except the report's answer suggestions."""
    return {role: settings.gemini_model for role in CallRole} | {
        CallRole.SUGGESTIONS: settings.gemini_suggestions_model
    }


def plan_quota(settings: Settings) -> QuotaPlan:
    """Worst case: the largest panel, and extraction runs (a client may omit
    domain and topic). The backend skips v1's relevance check."""
    calls = calls_per_model(
        worst_case_calls(MAX_PANEL_SIZE, extraction=True, relevance_check=False), role_models(settings)
    )
    shares = tuple(
        ModelShare(model=model, calls_per_session=n, rpd=settings.gemini_limits[model].rpd)
        for model, n in sorted(calls.items())
    )
    return QuotaPlan(shares=shares, margin=settings.quota_margin)


def log_plan(plan: QuotaPlan) -> None:
    for share in plan.shares:
        logger.info(
            "quota model=%s calls_per_session=%d rpd=%d sessions_per_day=%d",
            share.model, share.calls_per_session, share.rpd, share.sessions,
        )
    logger.info("quota daily_cap=%d binding_model=%s margin=%d", plan.cap, plan.binding.model, plan.margin)
    if plan.cap < LOW_CAP_WARNING:
        logger.warning(
            "QUOTA DAILY CAP IS %d (below %d): %s's RPD of %d covers only %d session(s) at %d calls each. "
            "Check ADS_GEMINI_MODEL and ADS_GEMINI_LIMITS.",
            plan.cap, LOW_CAP_WARNING, plan.binding.model, plan.binding.rpd, plan.binding.sessions,
            plan.binding.calls_per_session,
        )


class QuotaUnavailable(Exception):
    """The quota store couldn't be reached (Neon down or still waking)."""


class QuotaStore(Protocol):
    def used(self, day: date) -> int:
        """Sessions started on `day`."""
        ...

    def reserve(self, day: date, cap: int) -> int | None:
        """Takes one slot if fewer than `cap` are used: the new count, or None when full."""
        ...

    def close(self) -> None: ...


class InMemoryQuotaStore:
    """For tests and local runs without a database. Not durable."""

    def __init__(self) -> None:
        self._counts: dict[date, int] = {}
        self._lock = threading.Lock()

    def used(self, day: date) -> int:
        with self._lock:
            return self._counts.get(day, 0)

    def reserve(self, day: date, cap: int) -> int | None:
        with self._lock:
            count = self._counts.get(day, 0)
            if count >= cap:
                return None
            self._counts[day] = count + 1
            return count + 1

    def close(self) -> None:
        pass


class PostgresQuotaStore:
    """daily_usage in `schema` (tests use a throwaway one). The table is created on
    first use, so a database that was asleep at startup is picked up later."""

    def __init__(self, conninfo: str, *, schema: str = "public", max_size: int = 3, timeout_s: float = 10.0) -> None:
        self._timeout_s = timeout_s
        self._table = sql.Identifier(schema, "daily_usage")
        self._schema = sql.Identifier(schema)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        # Neon's pooled URL is PgBouncer in transaction mode, where server-side
        # prepared statements break; prepare_threshold=None turns them off.
        self._pool = ConnectionPool(
            conninfo, min_size=1, max_size=max_size, open=False, timeout=timeout_s,
            kwargs={"autocommit": True, "prepare_threshold": None, "connect_timeout": int(timeout_s)},
        )
        self._pool.open(wait=False)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connection() as conn:
                conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(self._schema))
                conn.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} (day date PRIMARY KEY, sessions integer NOT NULL DEFAULT 0)"
                    ).format(self._table)
                )
            self._schema_ready = True

    def used(self, day: date) -> int:
        self.ensure_schema()
        with self._connection() as conn:
            row = conn.execute(sql.SQL("SELECT sessions FROM {} WHERE day = %s").format(self._table), (day,)).fetchone()
        return row[0] if row else 0

    def reserve(self, day: date, cap: int) -> int | None:
        self.ensure_schema()
        with self._connection() as conn:
            conn.execute(
                sql.SQL("INSERT INTO {} (day) VALUES (%s) ON CONFLICT (day) DO NOTHING").format(self._table), (day,)
            )
            # The atomic step: Postgres locks the row for the UPDATE, so a second
            # concurrent reserve re-checks sessions < cap against the new count.
            row = conn.execute(
                sql.SQL(
                    "UPDATE {} SET sessions = sessions + 1 WHERE day = %s AND sessions < %s RETURNING sessions"
                ).format(self._table),
                (day, cap),
            ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        try:
            with self._pool.connection(timeout=self._timeout_s) as conn:
                yield conn
        except psycopg.Error as exc:  # includes the pool's PoolTimeout
            raise QuotaUnavailable(str(exc)) from exc


def warm_up(
    store: QuotaStore, *, attempts: int = 5, first_wait_s: float = 1.0, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """Create the schema at startup, retrying briefly while Neon wakes (1+2+4+8 s
    between 5 attempts). Never raises: if it gives up, the first request that
    needs the store tries again."""
    ensure = getattr(store, "ensure_schema", None)
    if ensure is None:
        return True
    wait_s = first_wait_s
    for attempt in range(1, attempts + 1):
        try:
            ensure()
            logger.info("quota store ready (attempt %d)", attempt)
            return True
        except QuotaUnavailable as exc:
            logger.warning("quota store not reachable (attempt %d of %d): %s", attempt, attempts, exc)
            if attempt < attempts:
                sleep(wait_s)
                wait_s *= 2
    logger.error("quota store still unreachable after %d attempts; will retry on first use", attempts)
    return False
