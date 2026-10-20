"""API request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from annotated_types import MaxLen
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY, PANEL_COMPOSITION


# v1's intake allows this many domain panelists; Devil's Advocate always joins them.
MAX_DOMAIN_PANELISTS = next(
    m.max_length for m in DefenseProfile.model_fields["selected_archetypes"].metadata if isinstance(m, MaxLen)
)


class PanelConfig(BaseModel):
    """Panel choices sent with an upload, used when the defense starts. Omitting
    the field entirely gives the defaults: a thesis defense, the recommended panel,
    the v1 slider's default starting difficulty, and domain and topic read from the
    document (one extraction call at start)."""

    model_config = ConfigDict(extra="forbid")

    defense_type: DefenseType = DefenseType.THESIS
    other_subtype: OtherSubtype | None = None  # required when defense_type is "other"
    domain: str | None = Field(default=None, min_length=1, max_length=200)
    topic: str | None = Field(default=None, min_length=1, max_length=300)
    # Domain panelists only; Devil's Advocate is always added last. None = the
    # recommended panel for the defense type (v1's "Use recommended panel").
    archetype_keys: list[str] | None = Field(default=None, min_length=1, max_length=MAX_DOMAIN_PANELISTS)
    starting_difficulty: int = Field(default=2, ge=1, le=5)

    @field_validator("domain", "topic", mode="before")
    @classmethod
    def _blank_is_missing(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _fits_the_defense_type(self) -> PanelConfig:
        if (self.defense_type is DefenseType.OTHER) != (self.other_subtype is not None):
            raise ValueError('other_subtype is required for defense_type "other", and only then')
        if self.archetype_keys is None:
            return self
        if DEVILS_ADVOCATE_KEY in self.archetype_keys:
            raise ValueError("devils_advocate is always on the panel; list only the domain panelists")
        if len(set(self.archetype_keys)) != len(self.archetype_keys):
            raise ValueError("archetype keys must not repeat")
        allowed = PANEL_COMPOSITION[self.composition_key]
        wrong = [k for k in self.archetype_keys if k not in allowed]
        if wrong:
            raise ValueError(f"{wrong} can't sit on a {self.composition_key} panel; choose from {allowed}")
        return self

    @property
    def composition_key(self) -> str:
        """v1's PANEL_COMPOSITION key: the defense type, or "other/<subtype>"."""
        if self.other_subtype is not None:
            return f"other/{self.other_subtype.value}"
        return self.defense_type.value

    def domain_archetypes(self) -> list[str]:
        """The chosen domain panelists, or the top of the recommended list."""
        return self.archetype_keys or PANEL_COMPOSITION[self.composition_key][:MAX_DOMAIN_PANELISTS]

    def to_profile(self, *, domain: str, topic: str, document_id: str) -> DefenseProfile:
        """v1's DefenseProfile, once domain and topic are known (sent, or extracted)."""
        return DefenseProfile(
            defense_type=self.defense_type,
            other_subtype=self.other_subtype,
            domain=domain,
            topic=topic,
            selected_archetypes=self.domain_archetypes(),
            difficulty_start=self.starting_difficulty,
            document_id=document_id,
        )


class Source(BaseModel):
    """The document passage a question was grounded in. Pages are 1-based, as a PDF
    viewer numbers them. `score` is the cosine similarity that retrieved the chunk;
    a follow-up or Devil's Advocate turn reuses an earlier chunk without retrieving,
    so it carries the score from that chunk's first retrieval, and `origin` says so."""

    text: str
    page_start: int
    page_end: int
    score: float
    origin: Literal["new_topic", "follow_up", "devils_advocate"]


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


class QuotaStatus(BaseModel):
    """GET /quota. A day is a Pacific calendar day; `resets_at` is its closing midnight."""

    used: int
    cap: int
    remaining: int
    resets_at: datetime


class PhaseMemory(BaseModel):
    phase: Literal["spooled", "parsed", "released", "embedded", "job_end"]
    rss_mb: float
    hwm_mb: float | None


class LastJob(BaseModel):
    session_id: str
    outcome: str
    pages: int
    chunks: int | None
    job_peak_rss_mb: float
    parse_s: float | None
    embed_s: float | None
    total_s: float
    phases: list[PhaseMemory]


class DebugMemory(BaseModel):
    """GET /debug/memory (ADS_DEBUG_MEMORY=1 only). pid plus start time identify
    the process, so a client can tell whether it restarted between two reads."""

    pid: int
    process_started_at: float
    commit: str | None
    max_upload_mb: float
    max_pages: int
    rss_mb: float
    hwm_mb: float | None
    last_job: LastJob | None
