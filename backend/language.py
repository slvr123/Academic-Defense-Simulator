"""The panel's language (v2.0 M16).

A session is English (the default) or Taglish, chosen at the start screen and carried
in `PanelConfig.panel_language`. Only what the panel says changes: its questions,
follow-ups, Devil's Advocate challenges and closing lines. Scoring, the debrief, answer
suggestions, retrieval and the interface stay English (Decision 1).

`ADS_TAGLISH_MODE` gates the choice: off, the client hides it and the API refuses a
Taglish panel (Decision 6).
"""

from __future__ import annotations

from typing import Literal

from backend.prompts.language_prompts import TAGLISH_LINE

PanelLanguage = Literal["english", "taglish"]


def question_language_line(language: PanelLanguage) -> str:
    """What fills v1's `{language_line}` slot: nothing for English, so the prompt is
    byte for byte today's; for Taglish, one more line of the panelist's brief, in the
    same "\\n- " form as the backend's `other_subtype_line`."""
    return "" if language == "english" else f"\n- Language: {TAGLISH_LINE}"


def closing_language_line(language: PanelLanguage) -> str:
    """What fills the closing prompt's `{language_line}` slot: the same line, on its own."""
    return "" if language == "english" else f"\n{TAGLISH_LINE}\n"
