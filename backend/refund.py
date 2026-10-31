"""v2.0 M11 P1: a defense slot is refunded when the defense never got its first question.

`/start` reserves before any Gemini call: one slot of the day's quota and one of the
caller's per-IP starts. That reservation is a session's `Charge`, and a session holds at
most one. It ends exactly once, in one of two ways:

- **released**: setup failed (`setup_failed`), or the session was deleted (`deleted`) or
  expired (`expired`) before a first question was delivered. Both counters are given
  back: the daily store, on the day the reservation was made (skipped, and logged, if
  the Pacific day has changed since), and the per-IP limiter.
- **final**: a first question was delivered. From then on nothing is refunded, whether
  the defense fails later, finishes, or is deleted.

The first of the two to happen wins, under the charge's own lock, so a DELETE racing a
`/start` still in flight can't both refund and deliver, and no path releases twice. A
process restart loses in-memory sessions, and their charges, without a refund (accepted).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from backend.client_ip import StartLimiter
from backend.quota import QuotaStore, QuotaUnavailable, pacific_day

if TYPE_CHECKING:
    from backend.sessions import Session

logger = logging.getLogger("backend.refund")

ReleaseReason = Literal["setup_failed", "deleted", "expired"]


@dataclass
class Charge:
    """One reservation: the Pacific day it was made on and the IP it counted against."""

    day: date
    ip: str
    _state: Literal["held", "released", "final"] = "held"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def settle(self, to: Literal["released", "final"]) -> bool:
        """Moves a held charge to `to`. False if it was already released or final."""
        with self._lock:
            if self._state != "held":
                return False
            self._state = to
            return True

    @property
    def state(self) -> str:
        return self._state


@dataclass(frozen=True)
class Refunder:
    quota: Callable[[], QuotaStore]  # read at release time: the store is created in the app's lifespan
    limiter: StartLimiter
    clock: Callable[[], datetime]

    def release(self, session: Session, reason: ReleaseReason) -> bool:
        """Gives back `session`'s charge if it's still held. True if this call released it."""
        charge = session.charge
        if charge is None or not charge.settle("released"):
            return False
        self.limiter.give_back(charge.day, charge.ip)  # a no-op once the limiter has moved to a new day
        today = pacific_day(self.clock())
        if today != charge.day:
            logger.info(
                "quota release session=%s reason=%s: daily release skipped, reserved on %s and today is %s",
                session.id, reason, charge.day, today,
            )
            return True
        try:
            count = self.quota().release(charge.day)
        except QuotaUnavailable as exc:
            logger.warning("quota release session=%s reason=%s: daily store unreachable, slot not returned: %s", session.id, reason, exc)
            return True
        logger.info("quota release session=%s reason=%s: now %d for %s", session.id, reason, count, charge.day)
        return True


def finalize(session: Session) -> None:
    """A first question was delivered: the session's charge can no longer be refunded."""
    if session.charge is not None:
        session.charge.settle("final")
