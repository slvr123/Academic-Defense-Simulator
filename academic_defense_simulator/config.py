"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str


def load_settings() -> Settings:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dependency.
        load_dotenv = None

    if load_dotenv is not None:
        load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return Settings(gemini_api_key=api_key)
