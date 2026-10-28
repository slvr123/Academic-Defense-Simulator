"""v2.0 M9 L4: the offline verdict on literature-review routing, from two eval files.

Usage (repo root, backend/.venv), after both runs of scripts/run_retrieval_eval.py:

    python scripts/check_routing_eval.py

Reads evals/v2.0-m6-retrieval-results.json (no routing: M8's results, which the plain
production run reproduces byte for byte) and evals/v2.0-m9-retrieval-results.json
(`--lit-review-access`). Pre-committed in docs/v2.0-m9-literature-routing-decisions.md,
L4, on sample3, DAZSMA and the bundled anicheck sample:
1. no top-1 pick and no session turn (plain or seeded) on a tagged chunk for any
   archetype but the Literature Specialist;
2. Check A's branch no worse on any document, Check E's max displacement (plain and
   seeded) at most 5, and no navigation or reference picks;
3. DAZSMA, which has no tagged chunks, byte-identical to M8's results.
Prints a table per document and `L4: pass` or `L4: fail` with the reasons.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "evals" / "v2.0-m6-retrieval-results.json"
AFTER = ROOT / "evals" / "v2.0-m9-retrieval-results.json"

DOCUMENTS = ["sample3.pdf", "Group2_Library Management System for DAZSMA Documentation (1).pdf", "sample-capstone-anicheck.pdf"]
UNTAGGED = "Group2_Library Management System for DAZSMA Documentation (1).pdf"
LITERATURE_SPECIALIST_KEY = "literature_theory_specialist"
BRANCH_ORDER = ["differentiated", "partial", "collapsed"]  # best first
MAX_DISPLACEMENT = 5


def _by_file(results: dict) -> dict[str, dict]:
    return {d["file"]: d for d in results["per_document"]}


def _canonical(value) -> str:
    return json.dumps(value, indent=1, sort_keys=True)


def check(before: dict, after: dict) -> tuple[list[str], list[str]]:
    """(report lines, failures)."""
    lines, failures = [], []
    old, new = _by_file(before), _by_file(after)
    for name in DOCUMENTS:
        if name not in old or name not in new:
            failures.append(f"{name}: missing from a results file")
            continue
        b, a, routing = old[name], new[name], after["literature_routing"][name]
        lines.append(f"{name} ({len(routing['tagged_chunks'])} tagged chunks, access={routing['access']})")
        lines.append(f"  {'archetype':34} {'top1':>6} {'plain':>6} {'seeded':>10}  (tagged picks, before -> after)")
        for archetype, picks in routing["per_archetype"].items():
            pb, pa = picks["before"], picks["after"]
            lines.append(
                f"  {archetype:34} {pb['top1']:>2} -> {pa['top1']:<1} {pb['plain_turns']:>2} -> {pa['plain_turns']:<1}"
                f" {pb['seeded_turns']:>3} -> {pa['seeded_turns']:<3} of {pa['seeded_turns_total']}"
            )
            if archetype != LITERATURE_SPECIALIST_KEY and (pa["top1"] or pa["plain_turns"] or pa["seeded_turns"]):
                failures.append(f"{name}: {archetype} still draws a tagged chunk")
        if "verdict" in b["check_a"]:
            vb, va = b["check_a"]["verdict"], a["check_a"]["verdict"]
            lines.append(f"  Check A: {vb} -> {va}")
            if BRANCH_ORDER.index(va) > BRANCH_ORDER.index(vb):
                failures.append(f"{name}: Check A moved from {vb} to {va}")
        plain = a["check_e"].get("max_displacement_rank")
        seeded = a.get("check_e_seeded", {}).get("max_displacement_rank")
        lines.append(f"  Check E max displacement: plain {b['check_e'].get('max_displacement_rank')} -> {plain}, "
                     f"seeded {b.get('check_e_seeded', {}).get('max_displacement_rank')} -> {seeded}")
        for value in (plain, seeded):
            if value is not None and value > MAX_DISPLACEMENT:
                failures.append(f"{name}: Check E displacement {value} > {MAX_DISPLACEMENT}")
        c, refs = a["check_c"], a.get("reference_filter", {})
        navigation = len(c.get("top1_navigation_picks", [])) + len(c.get("session_navigation_picks", []))
        references = refs.get("top1_reference_picks", 0) + refs.get("session_reference_turns", 0)
        avoided = a.get("check_e_seeded", {}).get("turns_on_avoided_chunks", 0)
        lines.append(f"  navigation picks {navigation}, reference picks {references}, seeded turns on avoided chunks {avoided}")
        if navigation or references or avoided:
            failures.append(f"{name}: navigation or reference picks")
    identical = _canonical(old.get(UNTAGGED)) == _canonical(new.get(UNTAGGED))
    lines.append(f"DAZSMA per-document results byte-identical to M8's: {identical}")
    if not identical:
        failures.append("DAZSMA's results changed")
    return lines, failures


def main() -> int:
    lines, failures = check(json.loads(BEFORE.read_text(encoding="utf-8")), json.loads(AFTER.read_text(encoding="utf-8")))
    print("\n".join(lines))
    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"L4: {'fail' if failures else 'pass'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
