"""v2.0 M7 J4: score the blinded attribution labels and print the pre-committed branch.

Usage (repo root, backend/.venv), once every item in the sheet has a label:

    python scripts/score_attribution_judging.py

Reads attribution-judging/sheet.md (the labels typed after each `Label:`) and
attribution-judging/key.json (which arm each item came from, and its grounding), both
written by scripts/probe_attribution.py. Labels: M (cited work treated as the
candidate's own), C (own work treated as cited), OK, ? (unsure).

Conservative, as J4 fixes it: an unsure label counts against the after arm and the
control (as M and C) and for the before arm (not as M). The branches, decided before
any data existed (docs/v2.0-m7-question-attribution-decisions.md, J4):
- before M <= 3 of 20: the problem is rare; leave the flag off, record it as a one-off;
- before M >= 4, after M <= 1, control C <= 1, after grounding >= before - 2: turn the flag on;
- anything else: leave it off; attribution goes back to design in chat.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUDGING = ROOT / "attribution-judging"
LABELS = ("M", "C", "OK", "?")
ARMS = ("before", "after", "control")

RARE, FLAG_ON, BACK_TO_DESIGN = "rare", "flag_on", "back_to_design"
BRANCH_TEXT = {
    RARE: "The problem is rare (before arm M <= 3 of 20). Don't turn the flag on; record it as a one-off.",
    FLAG_ON: "Turn the flag on: set ADS_ATTRIBUTION_NOTES=true on Render.",
    BACK_TO_DESIGN: "Leave the flag off. Attribution goes back to design in chat; no re-tries inside M7.",
}

_ITEM = re.compile(r"^## Item (\d+)\s*$")
_LABEL = re.compile(r"^Label:[ \t]*(.*?)\s*$")


class JudgingError(ValueError):
    """The sheet or key can't be scored as they stand; the message says what to fix."""


def parse_labels(sheet: str) -> dict[str, str]:
    """Item id -> label, from each item's `Label:` line. Every item needs exactly one valid label."""
    labels: dict[str, str] = {}
    item: str | None = None
    for line in sheet.splitlines():
        if heading := _ITEM.match(line):
            item = heading.group(1)
            continue
        if (found := _LABEL.match(line)) and item is not None:
            if item in labels:
                raise JudgingError(f"item {item} has two Label: lines")
            label = found.group(1).upper()
            if label not in LABELS:
                shown = found.group(1) or "(blank)"
                raise JudgingError(f"item {item}: label {shown!r} isn't one of M, C, OK, ?")
            labels[item] = label
    return labels


@dataclass(frozen=True)
class Score:
    labels: dict[str, Counter[str]]  # arm -> raw label counts
    before_m: int  # ? not counted (for the before arm)
    after_m: int  # ? counted as M (against the after arm)
    control_c: int  # ? counted as C (against the control)
    grounded: dict[str, int]  # arm -> grounding passes
    items: dict[str, int]  # arm -> items

    @property
    def branch(self) -> str:
        if self.before_m <= 3:
            return RARE
        if self.after_m <= 1 and self.control_c <= 1 and self.grounded["after"] >= self.grounded["before"] - 2:
            return FLAG_ON
        return BACK_TO_DESIGN


def score(labels: dict[str, str], key: dict[str, dict]) -> Score:
    if set(labels) != set(key):
        missing, extra = sorted(set(key) - set(labels)), sorted(set(labels) - set(key))
        raise JudgingError(f"labels and key don't match: unlabelled {missing}, not in the key {extra}")
    counts = {arm: Counter() for arm in ARMS}
    grounded = dict.fromkeys(ARMS, 0)
    items = dict.fromkeys(ARMS, 0)
    for item, entry in key.items():
        arm = entry["arm"]
        counts[arm][labels[item]] += 1
        grounded[arm] += bool(entry["grounded"])
        items[arm] += 1
    return Score(
        labels=counts,
        before_m=counts["before"]["M"],
        after_m=counts["after"]["M"] + counts["after"]["?"],
        control_c=counts["control"]["C"] + counts["control"]["?"],
        grounded=grounded,
        items=items,
    )


def report(result: Score) -> str:
    lines = []
    for arm in ARMS:
        raw = ", ".join(f"{label} {result.labels[arm][label]}" for label in LABELS)
        lines.append(f"{arm:8} {result.items[arm]:2} items: {raw}; grounding passes {result.grounded[arm]}")
    lines += [
        f"before arm M (unsure not counted):        {result.before_m} of {result.items['before']}",
        f"after arm M (unsure counted as M):        {result.after_m} of {result.items['after']}",
        f"control C (unsure counted as C):          {result.control_c} of {result.items['control']}",
        f"grounding after vs before - 2:            {result.grounded['after']} vs {result.grounded['before'] - 2}",
        f"J4 branch: {result.branch}",
        BRANCH_TEXT[result.branch],
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
