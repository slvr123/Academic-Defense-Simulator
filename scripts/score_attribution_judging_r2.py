"""v2.0 M8 K3: score the round-2 attribution labels and print the pre-committed verdict.

Usage (repo root, backend/.venv), once every item in the sheet has a label:

    python scripts/score_attribution_judging_r2.py

Reads attribution-judging-r2/sheet.md (the labels typed after each `Label:`, parsed
as M7's scorer parses them) and attribution-judging-r2/key.json, both written by
scripts/probe_attribution_r2.py. `?` counts as M. The verdict, decided before any
data existed (docs/v2.0-m8-attribution-voice-look-decisions.md, K3):
- M <= 1 of 28, grounding passes >= 25 of 28 and no note-only grounding: flag_on;
- anything else: stop (the flag stays off; attribution is parked until after launch).
Per chunk it prints both panelists' labels, so it shows whether chunks 14 and 17
(round 1's remaining misses) were fixed. The invented-citation count is reported,
not part of the verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.score_attribution_judging import LABELS, JudgingError, parse_labels  # noqa: E402

JUDGING = ROOT / "attribution-judging-r2"
ITEMS = 28
MAX_M, MIN_GROUNDED = 1, 25
ROUND_ONE_MISSES = (14, 17)
SHORT = {"technical_implementation_reviewer": "TIR", "methodology_expert": "ME"}

FLAG_ON, STOP = "flag_on", "stop"
VERDICT_TEXT = {
    FLAG_ON: "Turn the flag on: set ADS_ATTRIBUTION_NOTES=true on Render.",
    STOP: "Leave the flag off. Attribution is parked until after launch; what to launch with is Sean's call in chat.",
}


@dataclass(frozen=True)
class Score:
    labels: Counter[str]
    m: int  # ? counted as M
    grounded: int
    note_only: int
    invented_items: list[str]
    per_chunk: dict[int, dict[str, str]]  # chunk -> archetype short name -> label
    pages: dict[int, list[int]]

    @property
    def verdict(self) -> str:
        if self.m <= MAX_M and self.grounded >= MIN_GROUNDED and self.note_only == 0:
            return FLAG_ON
        return STOP


def score(labels: dict[str, str], key: dict[str, dict]) -> Score:
    if set(labels) != set(key):
        missing, extra = sorted(set(key) - set(labels)), sorted(set(labels) - set(key))
        raise JudgingError(f"labels and key don't match: unlabelled {missing}, not in the key {extra}")
    if len(key) != ITEMS:
        raise JudgingError(f"the key has {len(key)} items; K3's verdict is defined for {ITEMS}")
    counts: Counter[str] = Counter(labels.values())
    per_chunk: dict[int, dict[str, str]] = {}
    pages: dict[int, list[int]] = {}
    for item, entry in key.items():
        per_chunk.setdefault(entry["chunk"], {})[SHORT[entry["archetype"]]] = labels[item]
        pages[entry["chunk"]] = entry["pages"]
    return Score(
        labels=counts,
        m=counts["M"] + counts["?"],
        grounded=sum(bool(e["grounded"]) for e in key.values()),
        note_only=sum(bool(e["note_only_grounding"]) for e in key.values()),
        invented_items=sorted(item for item, e in key.items() if e["invented_authors"]),
        per_chunk=dict(sorted(per_chunk.items())),
        pages=pages,
    )


def report(result: Score) -> str:
    lines = ["chunk  pages    TIR  ME"]
    for chunk, by in result.per_chunk.items():
        mark = "  (round 1 miss)" if chunk in ROUND_ONE_MISSES else ""
        pages = f"{result.pages[chunk][0]}-{result.pages[chunk][1]}"
        lines.append(f"{chunk:5}  {pages:7}  {by.get('TIR', '-'):3}  {by.get('ME', '-'):3}{mark}")
    raw = ", ".join(f"{label} {result.labels[label]}" for label in LABELS)
    invented = ", ".join(result.invented_items) or "none"
    lines += [
        f"labels: {raw}",
        f"M (unsure counted as M):     {result.m} of {ITEMS}   (flag_on needs <= {MAX_M})",
        f"grounding passes:            {result.grounded} of {ITEMS}  (flag_on needs >= {MIN_GROUNDED})",
        f"note-only grounding:         {result.note_only}         (flag_on needs 0)",
        f"invented citations (report only): {len(result.invented_items)} items ({invented})",
        f"K3 verdict: {result.verdict}",
        VERDICT_TEXT[result.verdict],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheet", type=Path, default=JUDGING / "sheet.md")
    parser.add_argument("--key", type=Path, default=JUDGING / "key.json")
    args = parser.parse_args(argv)
    try:
        result = score(
            parse_labels(args.sheet.read_text(encoding="utf-8")),
            json.loads(args.key.read_text(encoding="utf-8")),
        )
    except JudgingError as exc:
        print(f"Can't score yet: {exc}", file=sys.stderr)
        return 1
    print(report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
