"""v2.0 M9 L5: the Literature Specialist probe, with L3's note (cited researchers named).

Usage (repo root, backend/.venv; GEMINI_API_KEY via backend.settings):

    python scripts/probe_attribution_r3.py --max-calls 40

After L2 only the Literature & Theory Specialist draws literature-review chunks, so
this asks it about every chunk J2 tags `literature_review` in sample3 (14) and in the
bundled anicheck sample (2), at difficulty 3 with notes on: 16 question calls, plus
each document's extraction and persona calls. The machinery is M7's
(scripts/probe_attribution.py): production template, model and grounding check with
only retrieval bypassed, every call through CachedProvider (9 s pacing, backoff on
503/504, a 429 stops the run). This round keeps its own cache and ledger in
attribution-judging-r3/ and reads M7's and M8's caches without writing to them.

Writes, under attribution-judging-r3/ (gitignored, they carry passage text):
judge-sheet.md (item, PDF pages, question, passage; nothing about the panelist, the
note or the document, which is what the independent judge gets with the rubric),
key.json (item -> document, chunk, pages, grounding, invented surnames),
generations.jsonl, cache.jsonl, ledger.json. And evals/v2.0-m9-attribution-probe.json:
counts and chunk numbers, no text. The judge's labels go in judge-labels.md; score them
with scripts/score_attribution_judging_r3.py.
"""

from __future__ import annotations

import argparse
import json
import random
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
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType  # noqa: E402
from academic_defense_simulator.models.session import DefenseSession  # noqa: E402
from academic_defense_simulator.panel import generate_panel  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.engine_adapter import CITED_AUTHORS_NOTE, engine_chunks  # noqa: E402
from backend.pdf_ingest import parse_pdf  # noqa: E402
from backend.routing import LITERATURE_SPECIALIST_KEY  # noqa: E402
from backend.sessions import ChunkMeta  # noqa: E402
from backend.settings import Settings  # noqa: E402
from scripts.probe_attribution_r2 import invented_authors  # noqa: E402

DOCUMENTS = [
    m7.Document(ROOT / "sample3.pdf", DefenseType.THESIS),
    m7.Document(ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf", DefenseType.CAPSTONE),
]
EXPECTED_ITEMS = {"sample3.pdf": 14, "sample-capstone-anicheck.pdf": 2}
SHUFFLE_SEED = "v2.0-m9-l5"
OUT_DIR = ROOT / "attribution-judging-r3"
SUMMARY = ROOT / "evals" / "v2.0-m9-attribution-probe.json"
READ_ONLY_CACHES = (m7.CACHE, ROOT / "attribution-judging-r2" / "cache.jsonl")
NOTE_LIST_START = CITED_AUTHORS_NOTE.split("{")[0]

SHEET_HEADER = """# Judging sheet

Each item is one question a defense panelist asked a candidate, and the passage from the candidate's own document that the panelist was reading when asking it.
"""


def write_judge_sheet(items: list[dict]) -> None:
    parts = [SHEET_HEADER]
    for n, item in enumerate(items, start=1):
        quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in item["passage"].splitlines())
        parts.append(
            f"\n---\n\n## Item {n:02d}\n\n"
            f"- PDF pages: {item['pages'][0]}-{item['pages'][1]}\n\n"
            f"**Question:** {item['question']}\n\n"
            f"**Passage:**\n\n{quoted}\n"
        )
    (OUT_DIR / "judge-sheet.md").write_text("".join(parts), encoding="utf-8")


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
        cache=OUT_DIR / "cache.jsonl", ledger=OUT_DIR / "ledger.json", read_only_caches=READ_ONLY_CACHES,
    )
    print(f"{provider.spent} calls made in earlier runs; cap {args.max_calls}")

    items, per_document = [], []
    with (OUT_DIR / "generations.jsonl").open("w", encoding="utf-8") as generations:
        for document in DOCUMENTS:
            filtered = parse_pdf(document.path)
            metas = [ChunkMeta(i, c.text, c.page_start, c.page_end, a)
                     for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))]
            review = [i for i, a in enumerate(filtered.attributions) if a == "literature_review"]
            if len(review) != EXPECTED_ITEMS[document.path.name]:
                raise SystemExit(f"STOP: {document.path.name} has {len(review)} literature_review chunks")
            print(f"{document.path.name}: review chunks {review}")

            extracted = extract_document_profile([m.text for m in metas], provider)
            profile = DefenseProfile(
                defense_type=document.defense_type, domain=extracted.domain.strip() or "unspecified",
                topic=extracted.topic.strip() or document.path.stem, selected_archetypes=[LITERATURE_SPECIALIST_KEY],
                document_id=f"m9-probe-{document.path.stem}",
            )
            panel, persona_fallback = generate_panel(profile, [LITERATURE_SPECIALIST_KEY], provider)
            panelist = next(p for p in panel if p.archetype_key == LITERATURE_SPECIALIST_KEY)
            chunks = engine_chunks(np.zeros((len(metas), 1), dtype=np.float32), metas, attribution_notes=True)
            per_document.append({"document": document.path.name, "review_chunks": review, "persona_fallback": persona_fallback})

            for index in review:
                session = DefenseSession(profile=profile, panel=panel, difficulty_current=m7.DIFFICULTY)
                turn = _generate_question(
                    provider, session, chunks, m7._NoEncoder(), panelist, "", m7.MODEL, retriever=m7.fixed(index)
                )
                original = metas[index].text
                grounded = is_grounded(turn.grounding_reference, original)
                item = {
                    "document": document.path.name, "chunk": index,
                    "pages": [metas[index].page_start + 1, metas[index].page_end + 1],
                    "grounded": grounded,
                    "note_only_grounding": not grounded and is_grounded(turn.grounding_reference, turn.chunk_text),
                    "note_named_authors": NOTE_LIST_START in turn.chunk_text,
                    "invented_authors": invented_authors(turn.question, original),
                }
                items.append(item | {"question": turn.question, "passage": original})
                generations.write(json.dumps(item | {
                    "question": turn.question, "grounding_reference": turn.grounding_reference,
                }) + "\n")
                print(f"  {document.path.name[:12]} chunk {index:3} grounded={grounded} "
                      f"note_only={item['note_only_grounding']} named={item['note_named_authors']} "
                      f"invented={len(item['invented_authors'])} calls={provider.spent} cache_hits={provider.hits}")

    random.Random(SHUFFLE_SEED).shuffle(items)
    write_judge_sheet(items)
    key = {f"{n:02d}": {k: v for k, v in item.items() if k not in ("question", "passage")}
           for n, item in enumerate(items, start=1)}
    (OUT_DIR / "key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")

    summary = {
        "milestone": "v2.0 M9 L5", "model": m7.MODEL, "difficulty": m7.DIFFICULTY, "archetype": LITERATURE_SPECIALIST_KEY,
        "calls_this_run": calls.total, "calls_across_runs": provider.spent, "cache_hits_this_run": provider.hits,
        "documents": per_document,
        "items": len(items),
        "grounded": sum(i["grounded"] for i in items),
        "note_only_grounding": sum(i["note_only_grounding"] for i in items),
        "note_named_authors": sum(i["note_named_authors"] for i in items),
        "invented_citation_items": sorted(n for n, entry in key.items() if entry["invented_authors"]),
    }
    SUMMARY.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
