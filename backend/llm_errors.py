"""How a failed Gemini call surfaces to a defense request.

Every Gemini 429, per-minute or per-day, and every 503 UNAVAILABLE is "busy": the
route answers 503 with PANEL_BUSY_MESSAGE, doesn't record the turn, and never
touches daily_usage. Only the backend's own quota counter closes a day. The
per-minute limiter and the daily session cap keep both kinds of 429 rare, so
telling them apart isn't worth a classifier (see
docs/v2.0-m2-defense-api-decisions.md for the trade-off).
"""

from __future__ import annotations

from academic_defense_simulator.llm.provider import (
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMServiceUnavailableError,
)

PANEL_BUSY_MESSAGE = "The panel is busy right now. Please try again in a minute."
PANEL_BUSY_RETRY_AFTER_S = 60


def is_panel_busy(exc: LLMProviderError) -> bool:
    """True for a Gemini rate limit or overload, which a retry in a minute may clear."""
    return isinstance(exc, (LLMQuotaExhaustedError, LLMServiceUnavailableError))
