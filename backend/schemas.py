"""API request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from annotated_types import MaxLen
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.report import DefenseReport
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


class PanelistOut(BaseModel):
    archetype_key: str
    name: str  # surname; the client adds "Dr."
    title: str  # the archetype's role, e.g. "Methodology Expert"
    icon: str


class QuestionOut(BaseModel):
    turn: int  # 1-based
    panelist: PanelistOut
    question: str
    difficulty: int  # 1-5
    sources: list[Source]  # top-1 retrieval, as in v1


class StartResponse(BaseModel):
    domain: str
    topic: str
    panel: list[PanelistOut]  # Devil's Advocate last
    question: QuestionOut


class CurrentQuestion(BaseModel):
    """GET /sessions/{id}/current (M6 H5): where the defense is now, so a client whose
    answer was refused as stale can move to the open question without reloading."""

    done: bool
    question: QuestionOut | None  # the open question; None once the defense is done


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)
    # The question being answered (QuestionOut.turn). Optional, so older callers keep
    # working; when sent, a resend of the last answered turn replays its response and
    # any other turn but the open one is refused (M3b E13).
    turn: int | None = Field(default=None, ge=1)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("the answer is blank")
        return text.strip()


class Evaluation(BaseModel):
    """v1's AnswerScore on its own 1-5 scale; any 0-100 rescale is presentation."""

    clarity: int
    depth: int
    grounding: int
    total: int  # 3-15
    difficulty_delta: int  # -1, 0 or 1, applied to the next question
    primary_gap: str | None
    answer_summary: str


class AnswerResponse(BaseModel):
    evaluation: Evaluation
    next_question: QuestionOut | None
    done: bool


class TranscriptTurn(BaseModel):
    turn: int
    panelist: PanelistOut
    question: str
    difficulty: int
    sources: list[Source]
    answer: str
    evaluation: Evaluation


class ReportResponse(BaseModel):
    domain: str
    topic: str
    report: DefenseReport  # v1's report: averages, per-panelist sections, pushback, narrative, suggestions
    transcript: list[TranscriptTurn]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    model_loaded: bool
    tts_available: bool  # MIMO_API_KEY is set, so POST /tts can synthesize


class TtsRequest(BaseModel):
    """POST /tts. Names a question the backend generated; never carries text, so the
    endpoint can't be used to synthesize anything else (M3b E3)."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=64)
    turn: int = Field(ge=1)  # QuestionOut.turn


class SessionCreated(BaseModel):
    session_id: str
    status: Literal["queued"] = "queued"
    queue_position: int


# Machine-readable reasons a client branches on; `detail` / `error` stay the readable text.
LimitCode = Literal["per_ip_limit", "daily_cap"]
TtsErrorCode = Literal["tts_limit", "tts_failed", "tts_unavailable"]
AnswerErrorCode = Literal["stale_question"]
StartErrorCode = Literal["already_started"]
ErrorCode = Literal[LimitCode, TtsErrorCode, AnswerErrorCode, StartErrorCode]
IngestErrorCode = Literal["scanned_pdf"]


class SessionState(BaseModel):
    status: Literal["queued", "embedding", "ready", "failed"]
    defense: Literal["not_started", "in_progress", "done"] | None = None  # once ready
    queue_position: int | None = None
    progress: float | None = None
    chunk_count: int | None = None
    error: str | None = None
    error_code: IngestErrorCode | None = None  # with error, when the reason has a code


class ErrorBody(BaseModel):
    """Every error response. `detail` is FastAPI's: readable text, or a list of
    validation errors on a 422. `code` is set where a client needs to tell causes
    apart without reading the text (a 429's limit, a /tts failure, a stale answer)."""

    detail: str | list[dict[str, object]]
    code: ErrorCode | None = None
    limit: int | None = None  # with per_ip_limit: the starts a network gets per day (M6 H6)


class QuotaStatus(BaseModel):
    """GET /quota. A day is a Pacific calendar day; `resets_at` is its closing midnight."""

    used: int
    cap: int
    remaining: int
    resets_at: datetime


class ArchetypeOption(BaseModel):
    archetype_key: str
    title: str  # e.g. "Methodology Expert"
    icon: str  # the icon a panelist of this archetype gets, as in PanelistOut


class PanelRoster(BaseModel):
    """The archetypes a panel of this type can seat, in v1's priority order; the
    first `max_domain_panelists` are the recommended panel."""

    defense_type: DefenseType
    other_subtype: OtherSubtype | None
    archetypes: list[ArchetypeOption]


class PanelOptions(BaseModel):
    """GET /panel-options: what PanelConfig's archetype_keys can hold, per defense type."""

    max_domain_panelists: int
    devils_advocate: ArchetypeOption  # always seated last, never chosen
    rosters: list[PanelRoster]


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


class ChunkCopy(BaseModel):
    """The engine's copy of one defense's chunks (backend/engine_adapter.py)."""

    session_id: str
    chunks: int
    mb: float


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
    last_chunk_copy: ChunkCopy | None = None  # the most recently started defense's


class DebugClient(BaseModel):
    """GET /debug/client (ADS_DEBUG_CLIENT=1 only): how the per-IP limit sees a request."""

    peer: str | None  # the TCP peer; on Render, its proxy
    x_forwarded_for: list[str]  # every entry, left to right
    trusted_proxy_hops: int
    resolved_ip: str  # what the per-IP limit counts
