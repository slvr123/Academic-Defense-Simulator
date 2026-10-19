"""API request and response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG


class PanelConfig(BaseModel):
    """Panel choices sent with an upload. Validated and stored in M1; used from M2.
    Omitting the field entirely gives the defaults: the recommended panel at the
    v1 slider's default starting difficulty."""

    model_config = ConfigDict(extra="forbid")

    # None = the recommended panel, composed later from the document's defense profile.
    archetype_keys: list[str] | None = Field(default=None, min_length=1)
    starting_difficulty: int = Field(default=2, ge=1, le=5)

    @field_validator("archetype_keys")
    @classmethod
    def _known_unique_archetypes(cls, keys: list[str] | None) -> list[str] | None:
        if keys is None:
            return None
        unknown = [k for k in keys if k not in ARCHETYPE_CONFIG]
        if unknown:
            raise ValueError(f"unknown archetype keys {unknown}; expected some of {sorted(ARCHETYPE_CONFIG)}")
        if len(set(keys)) != len(keys):
            raise ValueError("archetype keys must not repeat")
        return keys


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    model_loaded: bool


class SessionCreated(BaseModel):
    session_id: str
    status: Literal["queued"] = "queued"
    queue_position: int


class SessionState(BaseModel):
    status: Literal["queued", "embedding", "ready", "failed"]
    queue_position: int | None = None
    progress: float | None = None
    chunk_count: int | None = None
    error: str | None = None
