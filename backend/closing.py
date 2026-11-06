"""The outgoing panelist's closing line on a hand-off (v2.0 M15).

A hand-off is a next question from a different panelist than the one just answered,
by the same `select_active_panelist` decision the engine makes (Decision 1). The
closing line is one call to the defense model, started at the same moment as the next
question's generation (Decision 2), and it never holds the turn up: if it fails, is
invalid, or isn't back within `GRACE_S` of the question being ready, the outgoing
panelist says a fixed neutral line instead (Decision 4).

`ADS_CLOSING_LINES`: "llm" (the call), "fixed" (the fixed line, no call) or "off" (no
closing line at all).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from academic_defense_simulator.llm.provider import LLMProvider
from academic_defense_simulator.models.defense_profile import DefenseProfile
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from backend.language import PanelLanguage, closing_language_line
from backend.prompts.closing_prompts import CLOSING_PROMPT, EARLIER_LINES
from backend.prompts.language_prompts import TAGLISH_FALLBACK_LINE

logger = logging.getLogger("backend.closing")

ClosingMode = Literal["llm", "fixed", "off"]

FALLBACK_LINE = "Thank you. That's all from me for now."
MAX_WORDS = 30
MAX_SENTENCES = 2
# How long after the next question is ready a closing call may still land (Decision 4).
GRACE_S = 1.0

# A sentence ends at . ! ? (or an ellipsis), followed by a space or the end of the line.
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")


class ClosingLine(BaseModel):
    """The structured answer the closing call asks for."""

    line: str


@dataclass(frozen=True)
class Closing:
    """What the outgoing panelist says. `source` "llm" is the model's line; "fixed" is
    the fallback, with `reason` saying why (or "flag" when ADS_CLOSING_LINES is fixed)."""

    line: str
    source: Literal["llm", "fixed"]
    reason: str | None = None


def fallback_line(language: PanelLanguage) -> str:
    """The fixed closing line in the panel's language (M16 Decision 1)."""
    return FALLBACK_LINE if language == "english" else TAGLISH_FALLBACK_LINE


def problem(line: str) -> str | None:
    """Why `line` can't be shown, or None: one or two sentences, at most 30 words, and
    not a question (the prompt's rules 4 and 5)."""
    text = line.strip()
    if not text:
        return "empty"
    if len(text.split()) > MAX_WORDS:
        return "too_long"
    if "?" in text:
        return "asks_a_question"
    sentences = len(_SENTENCE_END.findall(text)) + (0 if _SENTENCE_END.search(text[-1:]) else 1)
    if sentences > MAX_SENTENCES:
        return "too_many_sentences"
    return None


def closing_prompt(
    panelist: Panelist,
    profile: DefenseProfile,
    question: str,
    answer: str,
    earlier: Sequence[str] = (),
    language: PanelLanguage = "english",
) -> str:
    """The closing prompt for the outgoing `panelist`. v1's own archetype title, as its
    question prompts use, so the voice matches; the display title is the API's concern.
    `earlier`: the closing lines already said in this session, in order (fallbacks
    included, since they were said too). `language` (M16): English adds nothing, so an
    English prompt is exactly M15's."""
    earlier_lines = EARLIER_LINES.format(lines="\n".join(f'- "{line}"' for line in earlier)) if earlier else ""
    return CLOSING_PROMPT.format(
        panelist_name=panelist.panelist_name,
        archetype_title=ARCHETYPE_CONFIG[panelist.archetype_key]["archetype_title"],
        defense_type=profile.defense_type.value,
        persona_framing=panelist.persona_framing,
        topic=profile.topic,
        question=question,
        answer=answer,
        earlier_lines=earlier_lines,
        language_line=closing_language_line(language),
    )


@dataclass
class ClosingCall:
    """One closing call on its own thread, started before the next question is
    generated. `take()` waits at most GRACE_S more once the question is ready."""

    provider: LLMProvider
    prompt: str
    fallback: str = FALLBACK_LINE  # the fixed line in the panel's language (M16)
    started: float = field(default_factory=time.perf_counter)
    _done: threading.Event = field(default_factory=threading.Event)
    _line: str | None = None
    _error: str | None = None
    _call_ms: int | None = None

    def start(self) -> ClosingCall:
        threading.Thread(target=self._run, name="closing-call", daemon=True).start()
        return self

    def _run(self) -> None:
        try:
            self._line = self.provider.generate_structured(self.prompt, ClosingLine).line
        except Exception as exc:  # noqa: BLE001 - any failure means the fallback, never a failed turn
            self._error = type(exc).__name__
        finally:
            self._call_ms = round((time.perf_counter() - self.started) * 1000)
            self._done.set()

    def take(self, grace_s: float = GRACE_S) -> tuple[Closing, int]:
        """The closing line, and how many ms it held the turn up beyond the question."""
        waited_from = time.perf_counter()
        landed = self._done.wait(grace_s)
        waited_ms = round((time.perf_counter() - waited_from) * 1000)
        if not landed:
            return Closing(self.fallback, "fixed", "timeout"), waited_ms
        if self._error is not None:
            return Closing(self.fallback, "fixed", f"error:{self._error}"), waited_ms
        assert self._line is not None
        reason = problem(self._line)
        if reason is not None:
            return Closing(self.fallback, "fixed", reason), waited_ms
        return Closing(self._line.strip(), "llm"), waited_ms

    @property
    def call_ms(self) -> int | None:
        """The call's own duration, once it has finished; None while it runs."""
        return self._call_ms


def log_closing(session_id: str, turn: int, archetype_key: str, closing: Closing, call_ms: int | None, waited_ms: int) -> None:
    """One line per hand-off. Counts and timings only: never the line, the answer or a passage."""
    log = logger.warning if closing.source == "fixed" and closing.reason not in (None, "flag") else logger.info
    log(
        "closing session=%s turn=%d archetype=%s source=%s reason=%s words=%d call_ms=%s waited_ms=%d",
        session_id, turn, archetype_key, closing.source, closing.reason or "-", len(closing.line.split()),
        "-" if call_ms is None else call_ms, waited_ms,
    )
