"""WCAG contrast for every text/background pair the themes use: v2.0 M8 K6, rewritten
for M12 D4's two themes, Paper (light, the @theme block) and Chamber (dark, the
[data-theme="chamber"] block), plus the print tokens.

Usage (repo root, any Python 3.11+):

    python scripts/check_contrast.py

Reads the colour tokens from frontend/src/index.css and prints each pair's ratio per
theme against its bar: 4.5:1 for body text, 3:1 for the non-text marks the design relies
on (the active card's accent border, the focus ring). The Devil's Advocate's dashed
border is decorative in both themes (its card is also labelled "Speaks last" and by
name), so it is reported, not required. Exit status 1 if any required pair fails.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "index.css"

BODY, LARGE = 4.5, 3.0

# (foreground token, background token, bar, where it's used); bar None: reported only.
PAIRS = [
    ("ink", "ground", BODY, "page text"),
    ("ink", "card", BODY, "card text, questions"),
    ("ink", "field", BODY, "answer box text"),
    ("ink", "accent-soft", BODY, "selected preset, hovered menu row"),
    ("ink-dim", "field", BODY, "your answer, recorded"),
    ("muted", "card", BODY, "secondary text on cards, panel card role and status"),
    ("muted", "ground", BODY, "header metadata"),
    ("muted", "field", BODY, "score labels, drop zone limits"),
    ("muted", "accent-soft", BODY, "pressed preset's members"),
    ("accent-text", "card", BODY, "'Dr. X asks', current step, 'Speaking'"),
    ("accent-text", "ground", BODY, "'Recorded demo' badge, drop zone 'browse'"),
    ("accent-text", "accent-soft", BODY, "'Overall' label"),
    ("ink", "card", BODY, "shimmer band passing over 'Reviewing'"),
    ("on-accent", "accent", BODY, "primary button"),
    ("on-accent", "accent-hover", BODY, "primary button, hover"),
    ("muted", "rail", BODY, "disabled button"),
    ("paper-ink", "paper", BODY, "source passage"),
    ("paper-ink", "highlight", BODY, "highlighted reference"),
    ("paper-muted", "paper", BODY, "passage page label"),
    ("ink", "warn-soft", BODY, "notice text"),
    ("ink", "danger-soft", BODY, "error notice text"),
    ("danger", "ground", BODY, "'no defenses left' line"),
    ("accent", "card", LARGE, "lit card border, focus ring (non-text)"),
    ("accent", "ground", LARGE, "focus ring on the page (non-text)"),
    ("devil-line", "surface", None, "Devil's Advocate dashed card (decorative)"),
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
    ("ink", "accent-soft", BODY, "print: a question's Overall score tile"),
    ("accent", "accent-soft", BODY, "print: the tile's 'Overall' label"),
    ("line-strong", "card", LARGE, "print: card borders (non-text)"),
]

TOKEN = r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})"


def tokens(theme: str) -> dict[str, str]:
    """'paper': the @theme block; 'chamber': Paper overridden by [data-theme="chamber"];
    'print': Paper overridden by @media print's."""
    css = CSS.read_text(encoding="utf-8")
    screen, _, printed = css.partition("@media print")
    selector = ':root[data-theme="chamber"] {'
    paper_block = screen.split(selector)[0]
    found = dict(re.findall(TOKEN, paper_block))
    if theme == "chamber":
        chamber = screen.split(selector, 1)[1].split("}", 1)[0]
        found.update(re.findall(TOKEN, chamber))
    elif theme == "print":
        found.update(re.findall(TOKEN, printed))
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
    checked = 0
    print(f"{'theme':8} {'foreground':13} {'background':12} {'ratio':>6} {'bar':>4}  use")
    for theme, pairs in (("paper", PAIRS), ("chamber", PAIRS), ("print", PRINT_PAIRS)):
        colours = tokens(theme)
        for fg, bg, bar, use in pairs:
            value = ratio(colours[fg], colours[bg])
            if bar is None:
                verdict, shown = "reported", "  - "
            else:
                verdict = "ok" if value >= bar else "FAIL"
                shown = f"{bar:4.1f}"
                checked += 1
            failed += verdict == "FAIL"
            print(f"{theme:8} {fg:13} {bg:12} {value:6.2f} {shown}  {use} {verdict}")
    print(f"{checked} required pairs, {failed} failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
