"""WCAG contrast for every text/background pair the themes use: v2.0 M8 K6, rewritten
for M12 D4's two themes, Paper (light, the @theme block) and Chamber (dark, the
[data-theme="chamber"] block), plus the print tokens.

Usage (repo root, any Python 3.11+):

    python scripts/check_contrast.py

Reads the colour tokens from frontend/src/index.css and prints each pair's ratio per
theme against its bar: 4.5:1 for body text, 3:1 for the non-text marks the design relies
on (the lit card's red border, the ink focus ring and selected borders). M13 D0 split the
one accent into two roles: ink (action-*) for what the user operates, panel red (accent)
for the panel only. The Devil's Advocate's dashed
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
    ("ink", "action-soft", BODY, "selected preset, hovered menu row"),
    ("ink-dim", "field", BODY, "your answer, recorded"),
    ("muted", "card", BODY, "secondary text on cards, panel card role and status"),
    ("muted", "ground", BODY, "header metadata"),
    ("muted", "field", BODY, "score labels, drop zone limits"),
    ("muted", "action-soft", BODY, "pressed preset's members"),
    # Ink (M13 D0): what the user operates.
    ("on-action", "action", BODY, "primary button"),
    ("on-action", "action-hover", BODY, "primary button, hover"),
    ("on-action", "action-pressed", BODY, "primary button, pressed"),
    ("action-text", "card", BODY, "current step, 'See the passage', 'Overall' heading"),
    ("action-text", "ground", BODY, "'Recorded demo' badge"),
    ("action-text", "field", BODY, "drop zone 'browse' link"),
    ("action-text", "action-soft", BODY, "'Overall' score tile label"),
    ("action-band", "card", BODY, "shimmer band over the current step 'Reviewing'"),
    ("muted", "rail", BODY, "disabled button"),
    ("action", "card", LARGE, "selected border, focus ring, radio dot (non-text)"),
    ("action", "ground", LARGE, "focus ring on the page (non-text)"),
    # Panel red: only the panel.
    ("accent-text", "card", BODY, "'Dr. X asks', 'Speaking'"),
    ("accent-text", "ground", BODY, "red text on the page background"),
    ("ink", "card", BODY, "shimmer band passing over the lit card's 'Reviewing'"),
    ("accent", "card", LARGE, "lit card border, cited passage card (non-text)"),
    ("accent", "ground", LARGE, "lit card border on the page (non-text)"),
    ("paper-ink", "paper", BODY, "source passage"),
    ("paper-ink", "highlight", BODY, "highlighted reference"),
    ("ink", "highlight", BODY, "text on the highlight"),
    ("paper-muted", "paper", BODY, "passage page label"),
    ("ink", "warn-soft", BODY, "notice text"),
    ("ink", "danger-soft", BODY, "error notice text"),
    ("danger", "ground", BODY, "'no defenses left' line"),
    ("devil-line", "surface", None, "Devil's Advocate dashed card (decorative)"),
]

# M13 D0's own checks for Red Pen that no element draws today; Paper only, since Chamber
# never puts its red on the highlight.
PAPER_ONLY_PAIRS = [
    ("accent-text", "highlight", BODY, "decision check: red over the passage highlight"),
]

# M10 N11: the print stylesheet's own tokens (index.css, @media print), black on white.
PRINT_PAIRS = [
    ("ink", "card", BODY, "print: body text"),
    ("muted", "card", BODY, "print: secondary text"),
    ("subtle", "card", BODY, "print: labels"),
    ("paper-ink", "paper", BODY, "print: source passage"),
    ("paper-ink", "highlight", BODY, "print: highlighted reference (also underlined)"),
    ("paper-muted", "paper", BODY, "print: passage page label"),
    ("action-text", "card", BODY, "print: 'Overall' label"),
    ("ink", "action-soft", BODY, "print: a question's Overall score tile"),
    ("action-text", "action-soft", BODY, "print: the tile's 'Overall' label"),
    ("accent-text", "card", BODY, "print: a panelist's red label"),
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
    for theme, pairs in (("paper", PAIRS + PAPER_ONLY_PAIRS), ("chamber", PAIRS), ("print", PRINT_PAIRS)):
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
