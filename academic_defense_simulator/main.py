"""CLI orchestration for Academic Defense Simulator."""

from __future__ import annotations

import argparse

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.llm.gemini_provider import GeminiProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Academic Defense Simulator")
    parser.add_argument("--pdf", help="Path to the source defense PDF")
    parser.add_argument("--query", help="Optional question to ask the panel")
    return parser


def main() -> None:
    settings = load_settings()
    parser = build_parser()
    args = parser.parse_args()

    provider = GeminiProvider(api_key=settings.gemini_api_key)
    # Wiring stays here; feature logic lives in the package modules.
    if args.pdf:
        _ = args.pdf
    if args.query:
        _ = provider
        _ = args.query


if __name__ == "__main__":
    main()
