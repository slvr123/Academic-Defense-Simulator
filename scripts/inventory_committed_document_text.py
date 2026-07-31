"""Inventory of source-document text that is committed to this repo.

Source PDFs are gitignored, but committed probe JSONLs, session exports, eval
logs, and results docs carry verbatim retrieved passages from the documents the
pipeline was run against. Going public publishes those passages. This script
answers, per committed file: which source document, how many distinct passages,
and the longest contiguous passage in characters.

Written for the v1.0d close-out (Task 3, `docs/v1.0d-close-out-brief.md`) and
kept re-runnable so the inventory can be regenerated after any scrub rather
than re-derived by hand.

Run from anywhere:

    python scripts/inventory_committed_document_text.py

Every file is stored in a different shape, so each gets an explicit extractor
rather than one glob over `scripts/`: JSONL rows expose named fields, the
session export nests passages under `session.turns`, the review sheet is
rendered markdown, the eval logs are stdout, and the results doc embeds fenced
quote blocks. Adding a file is one `Source` entry.

Zero LLM calls, zero network, read-only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Source-document labels. The PDFs themselves are never committed (standing
# rule); these name which document a file's passages were retrieved from.
DAZSMA = "DAZSMA"          # Group2_Library Management System for DAZSMA Documentation (1).pdf
SAMPLE3 = "sample3.pdf"    # the TDOA wearable capstone
# v1.0.1: unlike the two above, this document IS committed, deliberately — it is
# synthetic project content with no third-party consent or licence attached
# (Appendix A, Decision 0 as superseded). Its passages are inventoried anyway:
# the point of this script is a complete account of what committing publishes,
# and "this exposure is fine" is a conclusion the table should support rather
# than an assumption that keeps a file out of it.
ANICHECK = "sample-capstone-anicheck.pdf (synthetic, committed)"


def _jsonl_fields(*fields: str) -> Callable[[Path], set[str]]:
    """Distinct non-empty string values of the named fields across every row."""

    def extract(path: Path) -> set[str]:
        passages: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for field in fields:
                value = row.get(field)
                if isinstance(value, str) and value.strip():
                    passages.add(value)
        return passages

    return extract


_CREDENTIAL_KEY_SUBSTRINGS = ("api_key", "apikey", "mimo", "secret", "credential", "token")


def _assert_no_credential_shaped_keys(data: object, path: Path) -> None:
    """v1.2.1 Decision 4: the v1.1 inventory regression check below (document_chunks
    redaction) extended to MIMO_API_KEY -- "the v1.1 inventory-script regression
    check ... is extended to assert no MIMO_API_KEY value appears in any persisted
    artifact." Walks the parsed JSON looking for any dict key whose name suggests a
    credential, so a future accidental inclusion is caught by name, not by having
    to already know what a real key value looks like."""
    if isinstance(data, dict):
        for key, value in data.items():
            lowered = key.lower()
            if any(s in lowered for s in _CREDENTIAL_KEY_SUBSTRINGS) and value:
                raise SystemExit(
                    f"{path.name}: key {key!r} looks credential-shaped and is non-empty. "
                    "MIMO_API_KEY (and any other credential) must never appear in a "
                    "persisted artifact (v1.2.1 Decision 4)."
                )
            _assert_no_credential_shaped_keys(value, path)
    elif isinstance(data, list):
        for item in data:
            _assert_no_credential_shaped_keys(item, path)


def _session_export_turns(path: Path) -> set[str]:
    """`chunk_text` + `grounding_reference` across every turn of a persisted
    session export. `document_chunks` is deliberately not read: it is stripped
    to an empty list before commit (v0.4c Decision 6), and a non-empty one here
    would mean that redaction regressed -- so it is asserted instead."""
    data = json.loads(path.read_text(encoding="utf-8"))
    _assert_no_credential_shaped_keys(data, path)
    chunks = data.get("document_chunks")
    if chunks:
        raise SystemExit(
            f"{path.name}: document_chunks is non-empty ({len(chunks)} entries). "
            "The commit-time redaction has regressed -- this file would publish "
            "the document's full extracted text."
        )
    passages: set[str] = set()
    for turn in data["session"]["turns"]:
        for field in ("chunk_text", "grounding_reference"):
            value = turn.get(field)
            if isinstance(value, str) and value.strip():
                passages.add(value)
    return passages


_REVIEW_SHEET_RE = re.compile(r'\*\*Grounding reference:\*\* "(.*?)"')


def _review_sheet(path: Path) -> set[str]:
    """Grounding references as rendered into the human-judging review sheet."""
    body = path.read_text(encoding="utf-8")
    return {m for m in _REVIEW_SHEET_RE.findall(body) if m.strip()}


_EVAL_LOG_RE = re.compile(r'\[Grounding: "(.*?)" .{0,3}difficulty', re.DOTALL)


def _eval_log(path: Path) -> set[str]:
    """`[Grounding: "..." - difficulty N/5]` lines from a CLI-driver eval run."""
    body = path.read_text(encoding="utf-8", errors="replace")
    return {m.strip() for m in _EVAL_LOG_RE.findall(body) if m.strip()}


def _markdown_quote_blocks(path: Path) -> set[str]:
    """Fenced blocks holding verbatim chunk text quoted into prose.

    Line-based rather than regex, because these fences are indented inside list
    items. Blocks whose content is a JSON record are excluded: those are raw
    probe rows pasted from the JSONL files, already inventoried at their source,
    and counting them here would double-count the same passages.
    """
    passages: set[str] = set()
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.strip().startswith("```"):
            if current is None:
                current = []
            else:
                block = "".join(current)
                if block.strip() and not block.strip().startswith("{"):
                    passages.add(block)
                current = None
            continue
        if current is not None:
            current.append(line)
    return passages


@dataclass(frozen=True)
class Source:
    path: str
    document: str
    extract: Callable[[Path], set[str]]


SOURCES: tuple[Source, ...] = (
    Source("scripts/probe_difficulty_tone_results.jsonl", SAMPLE3, _jsonl_fields("grounding_reference")),
    Source("scripts/probe_followup_attribution_results.jsonl", DAZSMA, _jsonl_fields("grounding_reference")),
    Source("scripts/probe_question_gen_results.jsonl", DAZSMA, _jsonl_fields("grounding_reference")),
    Source(
        "scripts/probe_question_gen_v0.3_hardening_results.jsonl",
        DAZSMA,
        _jsonl_fields("grounding_reference", "target_chunk_text"),
    ),
    Source("scripts/probe_question_gen_v0.3_results.jsonl", DAZSMA, _jsonl_fields("grounding_reference")),
    Source("scripts/probe_question_gen_v0.4_sample3_results.jsonl", SAMPLE3, _jsonl_fields("grounding_reference")),
    Source("scripts/probe_question_gen_v1.1a_results.jsonl", DAZSMA, _jsonl_fields("grounding_reference")),
    Source("scripts/probe_question_gen_v1.1a_review.md", DAZSMA, _review_sheet),
    Source("scripts/v0_4c_stage_c_sample3_session.json", SAMPLE3, _session_export_turns),
    # v1.0.1 brief step 7: the committed example-session fixture. Same extractor
    # as the export above, so it gets the same document_chunks assertion — this
    # file is the exact shape that leaked a 3,827-char chunk at the v1.0 close-out.
    Source("academic_defense_simulator/sample/example_session.json", ANICHECK, _session_export_turns),
    Source("docs/v0.2-eval-results.md", DAZSMA, _markdown_quote_blocks),
    Source("docs/eval_run_1.log", DAZSMA, _eval_log),
    Source("docs/eval_run_2.log", DAZSMA, _eval_log),
)


def _tracked_files() -> set[str]:
    """Everything git tracks, so an untracked file can never be reported as
    committed exposure. A missing/!git environment degrades to an empty set and
    the check is skipped rather than failing the inventory."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    return set(out.splitlines())


def main() -> int:
    tracked = _tracked_files()
    rows: list[tuple[str, str, str, str]] = []
    untracked: list[str] = []
    total_passages = 0

    for source in SOURCES:
        path = REPO_ROOT / source.path
        if not path.is_file():
            raise SystemExit(f"missing: {source.path}")
        if tracked and source.path not in tracked:
            untracked.append(source.path)
        passages = source.extract(path)
        longest = max((len(p) for p in passages), default=0)
        total_passages += len(passages)
        rows.append((source.path, source.document, str(len(passages)), str(longest)))

    header = ("file", "source document", "distinct passages", "longest (chars)")
    widths = [max(len(header[i]), max(len(r[i]) for r in rows)) for i in range(4)]

    def line(cells: Iterable[str]) -> str:
        cells = list(cells)
        return "  ".join(
            cells[i].ljust(widths[i]) if i < 3 else cells[i].rjust(widths[i])
            for i in range(4)
        )

    print(line(header))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(line(row))

    worst = max(rows, key=lambda r: int(r[3]))
    print(
        f"\n{len(rows)} files inventoried; {total_passages} distinct passages total; "
        f"longest single passage {worst[3]} chars in {worst[0]} ({worst[1]})."
    )

    if untracked:
        untracked_rows = [r for r in rows if r[0] in set(untracked)]
        published = total_passages - sum(int(r[2]) for r in untracked_rows)
        print(
            f"\nNOT COMMITTED ({len(untracked)} of {len(rows)}): "
            + ", ".join(untracked)
            + "\n  Gitignored and never committed to any branch, so their passages are"
            "\n  local-only and were never published. They are kept in this inventory"
            "\n  for continuity with the v1.0d Phase 1 table, which listed them before"
            "\n  this tracked-check existed."
            f"\n  Actually-published total: {published} distinct passages across "
            f"{len(rows) - len(untracked)} committed files."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
