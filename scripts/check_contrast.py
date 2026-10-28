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
    # v2.0 M10: pairs the new screens added (existing tokens only, N1).
    ("ink", "surface", BODY, "M10: panel card name, path buttons"),
    ("muted", "surface", BODY, "M10: panel card role and status, path button detail, demo note"),
    ("muted", "accent-soft", BODY, "M10: lit card role, pressed preset's members"),
    ("subtle", "ground", BODY, "M10: 'No paper handy?', word count on the phone send bar"),
    ("accent", "ground", BODY, "M10: 'Recorded demo' badge, drop zone 'browse'"),
    ("ink", "raised", BODY, "M10: voice menu items"),
    ("subtle", "field", BODY, "M10: drop zone limits line"),
    ("ink", "ground", BODY, "M10: phone PDF sheet title"),
    ("devil-line", "field", LARGE, "M10: drop zone dashed border (non-text)"),
    ("devil-line", "surface", LARGE, "M10: Devil's Advocate dashed card (non-text)"),
    ("subtle", "card", LARGE, "M10: unpressed preset ring (non-text)"),
]

# M10 N11: the print stylesheet's own tokens (index.css, @media print), black on white.
PRINT_PAIRS = [
    ("ink", "card", BODY, "print: body text"),
    ("muted", "card", BODY, "print: secondary text"),
    ("subtle", "card", BODY, "print: labels"),
    ("paper-ink", "paper", BODY, "print: source passage"),
    ("paper-ink", "highlight", BODY, "print: highlighted reference (also underlined)"),
    ("paper-muted", "paper", BODY, "print: passage page label"),
    ("accent", "card", BODY, "print: 'Overall' label"),
    ("line-strong", "card", LARGE, "print: card borders (non-text)"),
]

# Shades the Bench mockup used that this theme changed, with the mockup's value.
MOCKUP = {"devil-line": "#4A3A36"}


def tokens(print_media: bool = False) -> dict[str, str]:
    """The screen tokens, or with `print_media` the screen tokens overridden by @media print's."""
    screen, _, printed = CSS.read_text(encoding="utf-8").partition("@media print")
    found = dict(re.findall(r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})", screen))
    if print_media:
        found.update(re.findall(r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})", printed))
    return found


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
    failed = 0
    print(f"{'foreground':13} {'background':12} {'ratio':>6} {'bar':>4}  {'mockup':>12}  use")
    for pairs, colours in ((PAIRS, tokens()), (PRINT_PAIRS, tokens(print_media=True))):
        for fg, bg, bar, use in pairs:
            value = ratio(colours[fg], colours[bg])
            before = f"{ratio(MOCKUP[fg], colours[bg]):.2f} ({MOCKUP[fg]})" if fg in MOCKUP and pairs is PAIRS else ""
            verdict = "ok" if value >= bar else "FAIL"
            failed += verdict == "FAIL"
            print(f"{fg:13} {bg:12} {value:6.2f} {bar:4.1f}  {before:>12}  {use} {verdict}")
    print(f"{len(PAIRS) + len(PRINT_PAIRS)} pairs, {failed} failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
