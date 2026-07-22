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
