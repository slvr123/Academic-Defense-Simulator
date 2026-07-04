"""Defense profile domain types."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DefenseType(str, Enum):
    THESIS = "thesis"
    CAPSTONE = "capstone"
    OTHER = "other"


class OtherSubtype(str, Enum):
    ORAL_COMPS = "oral_comps"
    SCHOLARSHIP_PANEL = "scholarship_panel"
    CERTIFICATION_INTERVIEW = "certification_interview"
    GRANT_DEFENSE = "grant_defense"


class DefenseProfile(BaseModel):
    defense_type: DefenseType
    other_subtype: Optional[OtherSubtype] = None
    domain: str = Field(..., min_length=1, description="Free-text field/discipline")
    topic: str = Field(..., min_length=1, description="Short user-entered research title/summary")
    panel_size: int = Field(default=1, ge=1, le=5)
    difficulty_start: int = Field(default=2, ge=1, le=5)
    document_id: str

    @model_validator(mode="after")
    def check_other_subtype(self) -> DefenseProfile:
        if self.defense_type == DefenseType.OTHER and self.other_subtype is None:
            raise ValueError("other_subtype is required when defense_type is 'other'")
        return self
