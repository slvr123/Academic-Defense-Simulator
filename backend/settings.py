"""Every backend limit and path, read from ADS_-prefixed env vars with defaults.

The upload limits come from the M1 Render memory measurement
(docs/v2.0-m1-memory-results.md): DAZSMA, 26.43 MB and 224 pages, peaked at
346.6 MB VmHWM on Render free.

The Gemini limits are the free tier of my project as AI Studio showed them on
2026-10-20. They size the daily session cap (backend/quota.py) and the per-minute
pacing of v1's provider, so they must match the project the key belongs to.
DATABASE_URL and GEMINI_API_KEY have no ADS_ prefix: they are the names Render
and Neon use. Locally they can come from backend/.env (gitignored).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent

# v1 verified 13 RPM clean against flash-lite's 15 (gemini_provider._RPM_CEILING): its
# limiter peaks one call above its ceiling, so it runs this far under the real limit.
RPM_PACING_MARGIN = 2


class ModelLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    rpd: int = Field(ge=0)  # requests per day; resets at midnight Pacific
    rpm: int = Field(ge=1)  # requests per minute
    tpm: int | None = Field(default=None, ge=1)  # tokens per minute; recorded, not enforced


DEFAULT_GEMINI_LIMITS = {
    "gemini-3.1-flash-lite": ModelLimits(rpd=500, rpm=15, tpm=250_000),
    "gemini-2.5-flash": ModelLimits(rpd=20, rpm=5, tpm=250_000),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADS_", env_file=BACKEND_DIR / ".env", extra="ignore", frozen=True, populate_by_name=True
    )

    # Jobs allowed to wait behind the one being embedded; the next upload gets 503.
    queue_max_waiting: int = Field(default=3, ge=1)
    # Queued + embedding + ready sessions. Failed sessions hold no matrix and don't count.
    max_live_sessions: int = Field(default=5, ge=1)
    session_ttl_seconds: float = Field(default=1800, gt=0)
    sweep_interval_seconds: float = Field(default=60, gt=0)
    max_upload_mb: float = Field(default=30, gt=0)
    max_pages: int = Field(default=250, ge=1)
    ort_threads: int = Field(default=1, ge=1)
    # GET /debug/memory exists only when this is on (ADS_DEBUG_MEMORY=1); M1 measurement only.
    debug_memory: bool = False
    # GET /debug/client exists only when this is on (ADS_DEBUG_CLIENT=1): the X-Forwarded-For check.
    debug_client: bool = False
    # Defense starts per client IP per Pacific day (in memory, resets on restart).
    per_ip_daily_starts: int = Field(default=3, ge=1)
    # X-Forwarded-For entries appended by proxies in front of us; the client is this many
    # from the right. Render's chain is client -> Cloudflare -> Render's internal proxy,
    # so a browser's request arrives as [client, Cloudflare, Render internal]: 3, as
    # checked live on 2026-10-20 (decisions doc, item 12). 0 ignores the header.
    trusted_proxy_hops: int = Field(default=3, ge=0)
    model_dir: Path = BACKEND_DIR / "model"
    upload_dir: Path = Path(tempfile.gettempdir()) / "ads-uploads"

    # The defense model: extraction, personas, questions, scoring and the narrative.
    gemini_model: str = "gemini-3.1-flash-lite"
    # Answer suggestions only, as in v1, which pins them to 2.5-flash.
    gemini_suggestions_model: str = "gemini-2.5-flash"
    # ADS_GEMINI_LIMITS, as JSON: {"<model>": {"rpd": .., "rpm": ..}}. Replaces the defaults whole.
    gemini_limits: dict[str, ModelLimits] = Field(default_factory=lambda: dict(DEFAULT_GEMINI_LIMITS))
    # Sessions held back from the computed daily cap, for retries after errors.
    quota_margin: int = Field(default=1, ge=0)
    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    # Never logged or returned: SecretStr keeps it out of reprs, and only the provider reads it.
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")

    @model_validator(mode="after")
    def _limits_cover_the_models(self) -> Settings:
        missing = [m for m in (self.gemini_model, self.gemini_suggestions_model) if m not in self.gemini_limits]
        if missing:
            raise ValueError(f"ADS_GEMINI_LIMITS has no rpd/rpm for {missing}; the daily cap can't be computed")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    def rpm_ceiling(self, model: str) -> int:
        """What v1's per-minute limiter is told for `model`: its RPM less the margin."""
        return max(1, self.gemini_limits[model].rpm - RPM_PACING_MARGIN)
