"""v2.0 M8 K6: WCAG contrast for every text/background pair the Bench theme uses.

Usage (repo root, any Python 3.11+):

    python scripts/check_contrast.py

Reads the colour tokens from frontend/src/index.css and prints each pair's ratio
against its bar: 4.5:1 for body text, 3:1 for text 24 px and up and for the
non-text marks K6 relies on (the active plate's border, the dashed Devil's Advocate
plate, the focus ring). The mockup column shows the shade the Bench mockup used where
this theme changed it. Exit status 1 if any pair fails.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "index.css"

BODY, LARGE = 4.5, 3.0

# (foreground token, background token, bar, where it's used)
PAIRS = [
    ("ink", "ground", BODY, "page text"),
    ("ink", "card", BODY, "card text, questions"),
    ("ink", "field", BODY, "answer box text"),
    ("ink", "accent-soft", BODY, "lit name plate"),
    ("ink-dim", "raised", BODY, "unlit panelist name"),
    ("ink-dim", "field", BODY, "your answer, recorded"),
    ("ink-dim", "card", BODY, "transcript answers"),
    ("muted", "card", BODY, "secondary text on cards"),
    ("muted", "ground", BODY, "header metadata"),
    ("muted", "raised", BODY, "lit panelist's title"),
    ("muted", "field", BODY, "score labels"),
    ("subtle", "card", BODY, "grounded-in line"),
    ("subtle", "raised", BODY, "unlit panelist's title"),
    ("subtle", "field", BODY, "'Your answer' label"),
    ("faint", "card", BODY, "upcoming turn steps"),
    ("accent", "card", BODY, "'Dr. X asks', current step"),
    ("accent", "raised", BODY, "'Reviewing' on the plate"),
    ("accent", "accent-soft", BODY, "'Overall' label"),
    ("accent-hover", "card", BODY, "shimmer highlight band"),
    ("preparing", "raised", BODY, "'Preparing' on the plate"),
    ("on-accent", "accent", BODY, "primary button"),
    ("on-accent", "accent-hover", BODY, "primary button, hover"),
    ("muted", "rail", BODY, "disabled button (mockup: #9C927F, 4.15:1)"),
    ("paper-ink", "paper", BODY, "source passage"),
    ("paper-ink", "highlight", BODY, "highlighted reference"),
    ("paper-muted", "paper", BODY, "passage page label"),
    ("ink", "warn-soft", BODY, "notice text"),
    ("ink", "danger-soft", BODY, "error notice text"),
    ("danger", "card", BODY, "'no defenses left' line"),
    ("accent", "card", LARGE, "lit plate border, focus ring (non-text)"),
    ("devil-line", "raised", LARGE, "Devil's Advocate dashed plate (non-text)"),
]

# Shades the Bench mockup used that this theme changed, with the mockup's value.
MOCKUP = {"devil-line": "#4A3A36"}


def tokens() -> dict[str, str]:
    return dict(re.findall(r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})", CSS.read_text(encoding="utf-8")))


def luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def main() -> int:
    colours = tokens()
    failed = 0
    print(f"{'foreground':13} {'background':12} {'ratio':>6} {'bar':>4}  {'mockup':>12}  use")
    for fg, bg, bar, use in PAIRS:
        value = ratio(colours[fg], colours[bg])
        before = f"{ratio(MOCKUP[fg], colours[bg]):.2f} ({MOCKUP[fg]})" if fg in MOCKUP else ""
        verdict = "ok" if value >= bar else "FAIL"
        failed += verdict == "FAIL"
        print(f"{fg:13} {bg:12} {value:6.2f} {bar:4.1f}  {before:>12}  {use} {verdict}")
    print(f"{len(PAIRS)} pairs, {failed} failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
