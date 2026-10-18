"""Demo-mode caps (v0.4a Decision 5) — pure business logic, no Streamlit imports
(standing import boundary). A JSON day-counter on disk tracks how many demo
sessions have started today; a stale date rolls over to a fresh count of 0/1
rather than raising. Concurrency note (Decision 5, accepted): two racing
read-then-write calls can each read the same pre-increment count and undercount
by one in the worst case — not worth locking machinery at this scale.

Enforcement-honesty note (Decision 5, accepted): this file lives on the deploy
container's local disk, so it resets on a Streamlit Cloud container restart. The
threat model is accidental quota drain from casual visitors, not adversaries.

v0.4a amendment (demo caps env-configurable): the three cap values are read from
env vars on every call, not baked in at import time — same os.getenv-with-default
sourcing `config.load_settings()` uses for GEMINI_MODEL, just call-time here too
so a monkeypatched env var takes effect without a module reload."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

DEFAULT_COUNTER_PATH = Path(__file__).parent / "data" / "demo_counter.json"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def demo_max_turns() -> int:
    return _env_int("ADS_DEMO_MAX_TURNS", 4)


def demo_sessions_per_browser() -> int:
    return _env_int("ADS_DEMO_SESSIONS_PER_BROWSER", 1)


def demo_daily_session_cap() -> int:
    return _env_int("ADS_DEMO_DAILY_CAP", 8)


@dataclass(frozen=True)
class _CounterState:
    day: str
    count: int


def _today() -> str:
    return date.today().isoformat()


def _read(path: Path) -> _CounterState:
    if not path.exists():
        return _CounterState(day=_today(), count=0)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _CounterState(day=raw["date"], count=raw["count"])
    except (json.JSONDecodeError, KeyError, OSError):
        # A corrupt or half-written counter file must not crash intake — treat it
        # as an empty day, same as a missing file.
        return _CounterState(day=_today(), count=0)


def _write(path: Path, state: _CounterState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": state.day, "count": state.count}), encoding="utf-8")


def demo_sessions_used_today(path: Path = DEFAULT_COUNTER_PATH) -> int:
    """Current count after applying date rollover — read-only, never writes."""
    state = _read(path)
    return state.count if state.day == _today() else 0


def demo_available(path: Path = DEFAULT_COUNTER_PATH, cap: int | None = None) -> bool:
    if cap is None:
        cap = demo_daily_session_cap()
    return demo_sessions_used_today(path) < cap


def consume_demo_session(path: Path = DEFAULT_COUNTER_PATH) -> int:
    """Increments today's count (rolling over a stale date to 1 first) and
    returns the new count. Does not enforce the cap itself — callers check
    `demo_available()` first, the same check-then-act split `compose_panel`
    uses against `DefenseProfile`'s own prior validation."""
    state = _read(path)
    new_count = (state.count + 1) if state.day == _today() else 1
    _write(path, _CounterState(day=_today(), count=new_count))
    return new_count


class GrantWrite(Enum):
    """One step of a demo grant (v1.3a Decision 2), in the order a driver must apply
    them. Each is a separate write, and a driver run can be stopped between any two."""

    MARK_DEMO = "mark_demo"  # api_key_mode = "demo": the grant has begun
    COUNT_BROWSER = "count_browser"  # this browser session's allowance, +1
    SET_KEY = "set_key"  # active_api_key: the grant is complete
    COUNT_DAILY = "count_daily"  # consume_demo_session(): the worldwide daily cap, +1


@dataclass(frozen=True)
class GrantPlan:
    allowed: bool
    writes: tuple[GrantWrite, ...] = ()


def plan_demo_grant(
    *,
    has_key: bool,
    mode: str | None,
    browser_sessions_started: int,
    per_browser_limit: int,
    daily_available: bool,
) -> GrantPlan:
    """Decides what is left to do for a demo grant from session state alone, so a
    retry after a stopped run finishes the grant instead of starting a second one.

    - A key already set: the grant (or an own key) is complete; nothing to do.
    - Mode already "demo" with no key: a grant began and was stopped before its key
      write. The browser count may or may not have landed, and state cannot tell
      which, so it is not repeated (at worst one low); the key and the daily count
      have certainly not happened, because both come after the key write.
    - Otherwise a fresh grant: refused if either cap is reached, else all four
      writes in order.

    Under-counting by one is the accepted worst case (the caps are a speed bump,
    v0.4a Decision 5); no stop point makes either counter go up twice."""
    if has_key:
        return GrantPlan(allowed=True)
    if mode == "demo":
        return GrantPlan(allowed=True, writes=(GrantWrite.SET_KEY, GrantWrite.COUNT_DAILY))
    if browser_sessions_started >= per_browser_limit or not daily_available:
        return GrantPlan(allowed=False)
    return GrantPlan(
        allowed=True,
        writes=(GrantWrite.MARK_DEMO, GrantWrite.COUNT_BROWSER, GrantWrite.SET_KEY, GrantWrite.COUNT_DAILY),
    )
