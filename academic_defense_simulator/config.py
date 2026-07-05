"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Default scoring/question model. flash-lite for iterative dev (500 RPD free-tier vs
# 2.5-flash's 20 RPD); override GEMINI_MODEL=gemini-2.5-flash for final verification runs.
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str


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
    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    return Settings(gemini_api_key=api_key, gemini_model=model)
