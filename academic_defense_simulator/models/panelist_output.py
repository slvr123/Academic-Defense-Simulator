"""Panelist output schema."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PanelistQuestion:
    question: str
    context: str | None = None
    expected_depth: str | None = None
