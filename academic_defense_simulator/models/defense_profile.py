"""Defense profile domain types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DefenseType(str, Enum):
    THESIS = "thesis"
    DISSERTATION = "dissertation"
    PROJECT = "project"
    OTHER = "other"


class OtherSubtype(str, Enum):
    UNKNOWN = "unknown"
    CUSTOM = "custom"


@dataclass(frozen=True)
class DefenseProfile:
    title: str
    defense_type: DefenseType
    other_subtype: OtherSubtype | None = None
