"""Every backend limit and path, read from ADS_-prefixed env vars with defaults.

The upload limits are provisional (M1 brief): the final values come from the
Render memory measurement in docs/v2.0-m1-memory-results.md.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADS_", frozen=True)

    # Jobs allowed to wait behind the one being embedded; the next upload gets 503.
    queue_max_waiting: int = Field(default=3, ge=1)
    # Queued + embedding + ready sessions. Failed sessions hold no matrix and don't count.
    max_live_sessions: int = Field(default=5, ge=1)
    session_ttl_seconds: float = Field(default=1800, gt=0)
    sweep_interval_seconds: float = Field(default=60, gt=0)
    max_upload_mb: float = Field(default=20, gt=0)
    max_pages: int = Field(default=150, ge=1)
    search_top_k: int = Field(default=1, ge=1)  # v1's engine retrieves top 1
    ort_threads: int = Field(default=1, ge=1)
    model_dir: Path = BACKEND_DIR / "model"
    upload_dir: Path = Path(tempfile.gettempdir()) / "ads-uploads"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)
