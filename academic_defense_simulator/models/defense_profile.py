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


def _composition_key(defense_type: DefenseType, other_subtype: Optional[OtherSubtype]) -> str:
    if defense_type == DefenseType.OTHER:
        assert other_subtype is not None
        return f"other/{other_subtype.value}"
    return defense_type.value


class DefenseProfile(BaseModel):
    defense_type: DefenseType
    other_subtype: Optional[OtherSubtype] = None
    domain: str = Field(..., min_length=1, description="Free-text field/discipline")
    topic: str = Field(..., min_length=1, description="Short user-entered research title/summary")
    selected_archetypes: list[str] = Field(..., min_length=1, max_length=3)
    difficulty_start: int = Field(default=2, ge=1, le=5)
    document_id: str

    @model_validator(mode="after")
    def check_other_subtype(self) -> DefenseProfile:
        if self.defense_type == DefenseType.OTHER and self.other_subtype is None:
            raise ValueError("other_subtype is required when defense_type is 'other'")
        return self

    @model_validator(mode="after")
    def check_selected_archetypes(self) -> DefenseProfile:
        # Local import: panel.py imports DefenseProfile from this module at module
        # level, so importing PANEL_COMPOSITION from panel.py at module level here
        # would be circular. Deferring the import to call time (after both modules
        # have finished loading) breaks the cycle without restructuring either file.
        from academic_defense_simulator.panel import PANEL_COMPOSITION

        key = _composition_key(self.defense_type, self.other_subtype)
        allowed = set(PANEL_COMPOSITION[key])
        chosen = set(self.selected_archetypes)
        if not chosen.issubset(allowed):
            raise ValueError(
                f"selected_archetypes contains keys not valid for '{key}': "
                f"{chosen - allowed}"
            )
        if len(chosen) != len(self.selected_archetypes):
            raise ValueError("selected_archetypes contains duplicates")
        return self
