"""v2.0 M8 K3: the round-2 attribution probe, notes on, with M8 K2's note.

Usage (repo root, backend/.venv; GEMINI_API_KEY via backend.settings):

    python scripts/probe_attribution_r2.py --max-calls 80

All 14 chunks J2 tags `literature_review` in sample3, each asked by the Technical
Implementation Reviewer and the Methodology Expert at difficulty 3 with notes on:
28 question calls, plus the extraction and persona calls a real defense makes. No
"before" arm (M7 measured it) and no control (untagged chunks get no note, so they
can't change).

The machinery is M7's (scripts/probe_attribution.py): production template, model and
grounding check with only retrieval bypassed, and every call through CachedProvider
(9 s pacing, backoff on 503/504, a 429 stops the run). Round 2 keeps its own cache and
ledger in attribution-judging-r2/ and reads M7's cache without writing to it, so the
extraction and persona come from M7's run when their prompts are byte-identical, and
the panel is the one M7's items were asked by.

Each item also gets the invented-citation check (report only, not in the verdict):
an author surname the question names that doesn't appear in its passage, as M7's
item 39 credited a method to "Yarkan" in a Yang and Song passage.

Writes, under attribution-judging-r2/ (gitignored, they carry passage text): sheet.md,
key.json (item -> chunk, archetype, grounding, invented surnames), generations.jsonl,
cache.jsonl, ledger.json. And evals/v2.0-m8-attribution-probe.json: counts and chunk
numbers, no text. Score the labels with scripts/score_attribution_judging_r2.py.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import scripts.probe_attribution as m7  # noqa: E402
from academic_defense_simulator.document_profile import extract_document_profile  # noqa: E402
from academic_defense_simulator.engine import _generate_question  # noqa: E402
from academic_defense_simulator.grounding import is_grounded  # noqa: E402
from academic_defense_simulator.llm.provider import CallCounter  # noqa: E402
from academic_defense_simulator.models.defense_profile import DefenseProfile  # noqa: E402
from academic_defense_simulator.models.session import DefenseSession  # noqa: E402
from academic_defense_simulator.panel import generate_panel  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.engine_adapter import engine_chunks  # noqa: E402
from backend.pdf_ingest import parse_pdf  # noqa: E402
from backend.sessions import ChunkMeta  # noqa: E402
from backend.settings import Settings  # noqa: E402

DOCUMENT = m7.DOCUMENTS[0]  # sample3.pdf
EXPECTED_ITEMS = 28
SHUFFLE_SEED = "v2.0-m8-k3"
OUT_DIR = ROOT / "attribution-judging-r2"
SUMMARY = ROOT / "evals" / "v2.0-m8-attribution-probe.json"

# An author named the way a question names one: "proposed by Yarkan", "by Yang and Song",
# "Yarkan's method", "Reyes et al.", "Reyes (2021)". Initials before the surname are allowed.
_NAME = r"(?:[A-Z]\.\s?)*[A-Z][a-z][A-Za-z'’-]*"
_AUTHOR_CONTEXTS = [
    re.compile(rf"\bby\s+({_NAME})(?:\s+(?:and|&)\s+({_NAME}))?"),
    re.compile(rf"\b({_NAME})(?:\s+(?:and|&)\s+({_NAME}))?(?:'s|’s)\s"),
    re.compile(rf"\b({_NAME})\s+et\s+al\b"),
    re.compile(rf"\b({_NAME})(?:\s+(?:and|&)\s+({_NAME}))?\s+\((?:19|20)\d{{2}}[a-z]?\)"),
]
# Capitalised words that follow "by" or take "'s" in a question without being an author.
_NOT_AUTHORS = frozenset({"The", "This", "That", "These", "Those", "Your", "Its", "Their", "Each", "Using", "Chapter"})


def named_authors(question: str) -> list[str]:
    """Surnames the question puts in an author position, in order, without repeats."""
    names: list[str] = []
    for pattern in _AUTHOR_CONTEXTS:
        for match in pattern.finditer(question):
            for group in match.groups():
                if group is None:
                    continue
                surname = re.sub(r"(?:'s|’s)$", "", re.sub(r"^(?:[A-Z]\.\s?)+", "", group))
                if surname not in _NOT_AUTHORS and surname not in names:
                    names.append(surname)
    return names


def invented_authors(question: str, passage: str) -> list[str]:
    """Named authors whose surname doesn't appear anywhere in the passage (any case)."""
    return [name for name in named_authors(question)
            if not re.search(rf"(?i)\b{re.escape(name)}\b", passage)]


SHEET_HEADER = """# v2.0 M8 attribution judging sheet, round 2

Each item is one generated question and the passage it was generated from.
All 28 were generated with M8's round-2 note on; the items are shuffled.
For each item, type one label after `Label:`:

- `M`: the question treats work the candidate cites as the candidate's own.
- `C`: the question treats the candidate's own work as cited work.
- `OK`: neither.
- `?`: unsure (it counts as M).

Then run `backend\\.venv\\Scripts\\python scripts\\score_attribution_judging_r2.py` from the repo root.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-calls", type=int, required=True, help="the cap on attempts across every run")
    args = parser.parse_args()
    settings = Settings()
    if settings.gemini_model != m7.MODEL:
        raise SystemExit(f"STOP: the defense model is {settings.gemini_model}, not {m7.MODEL}")
    OUT_DIR.mkdir(exist_ok=True)
    calls = CallCounter()
    provider = m7.CachedProvider(
        gemini_providers_factory(settings)(calls).defense, calls, args.max_calls,
        cache=OUT_DIR / "cache.jsonl", ledger=OUT_DIR / "ledger.json", read_only_caches=(m7.CACHE,),
    )
    print(f"{provider.spent} calls made in earlier runs; cap {args.max_calls}")

    filtered = parse_pdf(DOCUMENT.path)
    metas = [ChunkMeta(i, c.text, c.page_start, c.page_end, a)
             for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))]
    review = [i for i, a in enumerate(filtered.attributions) if a == "literature_review"]
    if len(review) * len(m7.ARCHETYPES) != EXPECTED_ITEMS:
        raise SystemExit(f"STOP: {len(review)} tagged chunks, not {EXPECTED_ITEMS // len(m7.ARCHETYPES)}")
    print(f"{DOCUMENT.path.name}: review chunks {review}")

    extracted = extract_document_profile([m.text for m in metas], provider)
    profile = DefenseProfile(
        defense_type=DOCUMENT.defense_type, domain=extracted.domain.strip() or "unspecified",
        topic=extracted.topic.strip() or DOCUMENT.path.stem, selected_archetypes=m7.ARCHETYPES,
        document_id=f"m7-probe-{DOCUMENT.path.stem}",  # M7's id, so the persona prompt is M7's too
    )
    panel, persona_fallback = generate_panel(profile, m7.ARCHETYPES, provider)
    chunks = engine_chunks(np.zeros((len(metas), 1), dtype=np.float32), metas, attribution_notes=True)

    items = []
    with (OUT_DIR / "generations.jsonl").open("w", encoding="utf-8") as generations:
        for archetype in m7.ARCHETYPES:
            panelist = next(p for p in panel if p.archetype_key == archetype)
            for index in review:
                session = DefenseSession(profile=profile, panel=panel, difficulty_current=m7.DIFFICULTY)
                turn = _generate_question(
                    provider, session, chunks, m7._NoEncoder(), panelist, "", m7.MODEL, retriever=m7.fixed(index)
                )
                original = metas[index].text
                grounded = is_grounded(turn.grounding_reference, original)
                item = {
                    "document": DOCUMENT.path.name, "chunk": index, "archetype": archetype,
                    "pages": [metas[index].page_start + 1, metas[index].page_end + 1],
                    "grounded": grounded,
                    "note_only_grounding": not grounded and is_grounded(turn.grounding_reference, turn.chunk_text),
                    "invented_authors": invented_authors(turn.question, original),
                }
                items.append(item | {"question": turn.question, "passage": original})
                generations.write(json.dumps(item | {
                    "question": turn.question, "grounding_reference": turn.grounding_reference,
                    "prompt_saw_note": turn.chunk_text != original,
                }) + "\n")
                print(f"  chunk {index:3} {archetype:34} grounded={item['grounded']} "
                      f"note_only={item['note_only_grounding']} invented={len(item['invented_authors'])} "
                      f"calls={provider.spent} cache_hits={provider.hits}")

    random.Random(SHUFFLE_SEED).shuffle(items)
    m7.write_sheet(items, header=SHEET_HEADER, out_dir=OUT_DIR)
    key = {f"{n:02d}": {k: v for k, v in item.items() if k not in ("question", "passage")}
           for n, item in enumerate(items, start=1)}
    (OUT_DIR / "key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")

    summary = {
        "milestone": "v2.0 M8 K3", "model": m7.MODEL, "difficulty": m7.DIFFICULTY, "archetypes": m7.ARCHETYPES,
        "calls_this_run": calls.total, "calls_across_runs": provider.spent, "cache_hits_this_run": provider.hits,
        "persona_fallback": persona_fallback,
        "items": len(items),
        "grounded": sum(i["grounded"] for i in items),
        "note_only_grounding": sum(i["note_only_grounding"] for i in items),
        "invented_citation_items": sorted(n for n, entry in key.items() if entry["invented_authors"]),
        "per_chunk": [
            {
                "chunk": index, "pages": [metas[index].page_start + 1, metas[index].page_end + 1],
                "grounded": {i["archetype"]: i["grounded"] for i in items if i["chunk"] == index},
            }
            for index in review
        ],
    }
    SUMMARY.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
