"""v2.0 M9 L5: score the independent judge's labels and print the pre-committed verdict.

Usage (repo root, backend/.venv), once the judge's reply is saved:

    python scripts/score_attribution_judging_r3.py

Reads attribution-judging-r3/judge-labels.md (the judge's reply: one `NN | LABEL |
reason` line per item) and attribution-judging-r3/key.json, both gitignored. `?`
counts as M. The verdict, decided before any data existed
(docs/v2.0-m9-literature-routing-decisions.md, L5):
- M <= 1: the Literature Specialist keeps access (lit_review_access = "literature");
- anything else: no panelist draws literature-review passages ("none").
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUDGING = ROOT / "attribution-judging-r3"
LABELS = ("M", "C", "OK", "?")
MAX_M = 1
VERDICT_ACCESS = {True: "literature", False: "none"}
VERDICT_TEXT = {
    "literature": "The Literature Specialist keeps access: settings.lit_review_access defaults to \"literature\".",
    "none": "No panelist draws literature-review passages: settings.lit_review_access defaults to \"none\".",
}
_LINE = re.compile(r"^\s*(?P<item>\d{2})\s*\|\s*(?P<label>OK|M|C|\?)\s*\|\s*(?P<reason>.+?)\s*$")


class JudgingError(ValueError):
    pass


def parse_judge(text: str) -> dict[str, tuple[str, str]]:
    """item -> (label, reason). Refuses an item labelled twice."""
    labels: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        match = _LINE.match(line)
        if match is None:
            continue
        if match["item"] in labels:
            raise JudgingError(f"item {match['item']} is labelled twice")
        labels[match["item"]] = (match["label"], match["reason"])
    return labels


def score(labels: dict[str, tuple[str, str]], key: dict[str, dict]) -> tuple[Counter[str], int, str]:
    """(label counts, M with ? counted as M, access)."""
    if set(labels) != set(key):
        missing, extra = sorted(set(key) - set(labels)), sorted(set(labels) - set(key))
        raise JudgingError(f"labels and key don't match: unlabelled {missing}, not in the key {extra}")
    counts: Counter[str] = Counter(label for label, _ in labels.values())
    m = counts["M"] + counts["?"]
    return counts, m, VERDICT_ACCESS[m <= MAX_M]


def report(labels: dict[str, tuple[str, str]], key: dict[str, dict]) -> str:
    counts, m, access = score(labels, key)
    lines = ["item  document      chunk  pages    label"]
    for item in sorted(key):
        entry = key[item]
        pages = f"{entry['pages'][0]}-{entry['pages'][1]}"
        lines.append(f"{item:4}  {entry['document'][:12]:12}  {entry['chunk']:5}  {pages:7}  {labels[item][0]}")
    lines += [
        "labels: " + ", ".join(f"{label} {counts[label]}" for label in LABELS),
        f"M (unsure counted as M): {m} of {len(key)}   (the Literature Specialist keeps access at <= {MAX_M})",
        f"grounding passes: {sum(bool(e['grounded']) for e in key.values())} of {len(key)}",
        f"note-only grounding: {sum(bool(e['note_only_grounding']) for e in key.values())}",
        f"invented citations (report only): {sum(bool(e['invented_authors']) for e in key.values())} items",
        f"L5 verdict: {access}",
        VERDICT_TEXT[access],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", type=Path, default=JUDGING / "judge-labels.md")
    parser.add_argument("--key", type=Path, default=JUDGING / "key.json")
    args = parser.parse_args(argv)
    try:
        print(report(
            parse_judge(args.labels.read_text(encoding="utf-8")),
            json.loads(args.key.read_text(encoding="utf-8")),
        ))
    except JudgingError as exc:
        print(f"Can't score yet: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
