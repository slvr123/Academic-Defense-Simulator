"""v2.0 M7 J4: the blinded before/after probe for literature-review attribution notes.

Usage (repo root, backend/.venv; GEMINI_API_KEY via backend.settings):

    python scripts/probe_attribution.py --max-calls 80

On sample3 and DAZSMA, through the production ingest and question path:
- 10 literature-review chunks that cite a specific study (an "et al." or an
  "(Author, YYYY)" citation), evenly spaced over each document's candidates. J4 asks
  for 5 per document, but DAZSMA's tagged Chapter 2 has only one such chunk, so its
  shortfall moves to sample3 (a deviation recorded in docs/v2.0-m7-results.md);
- per document, 3 of the candidate's own chunks from Chapter 3 (untagged, from the
  Chapter 3 heading to the next chapter heading, or to the reference list);
each asked by the Technical Implementation Reviewer and the Methodology Expert at
difficulty 3. Literature-review items run twice, notes off (before) and on (after);
control items once, notes on. That's 20 + 20 + 12 = 52 question calls, plus one
extraction and one persona call per document, as a real defense makes them.

The chunk is fixed through `_generate_question`'s `retriever` (M6 H3), so the
template, model, settings and grounding check are production's and only retrieval
is bypassed. Calls are paced 9 s apart: a question call is at most two attempts
(GeminiProvider retries once), and two per 9 s stays under 15 a minute. Stops
before any call whose worst case could take the total past --max-calls.

Writes, all under attribution-judging/ (gitignored, they carry passage text):
- sheet.md: the shuffled items with no arm labels, a blank label each;
- key.json: item -> arm, document, chunk, grounding;
- generations.jsonl: every raw generation.
And evals/v2.0-m7-attribution-probe.json: counts and chunk choices, no text.
Score the labels with scripts/score_attribution_judging.py.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402  PyMuPDF
import numpy as np  # noqa: E402

from academic_defense_simulator.document_profile import extract_document_profile  # noqa: E402
from academic_defense_simulator.engine import _generate_question  # noqa: E402
from academic_defense_simulator.grounding import is_grounded  # noqa: E402
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError  # noqa: E402
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType  # noqa: E402
from academic_defense_simulator.models.session import DefenseSession  # noqa: E402
from academic_defense_simulator.panel import generate_panel  # noqa: E402
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG  # noqa: E402
from academic_defense_simulator.rag.retrieval import Chunk  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.engine_adapter import engine_chunks  # noqa: E402
from backend.page_filter import FilteredChunks  # noqa: E402
from backend.pdf_ingest import parse_pdf  # noqa: E402
from backend.sessions import ChunkMeta  # noqa: E402
from backend.settings import Settings  # noqa: E402

MODEL = "gemini-3.1-flash-lite"
ARCHETYPES = ["technical_implementation_reviewer", "methodology_expert"]
DIFFICULTY = 3
REVIEW_ITEMS, REVIEW_ITEMS_PER_DOCUMENT, CONTROL_ITEMS = 10, 5, 3
PACE_S = 9.0
# Gemini answered 503 "high demand" on the first run (2026-10-27): an item that fails
# after the provider's own retry waits a minute and is tried again, twice at most.
ITEM_TRIES, BUSY_WAIT_S = 3, 60.0
# GeminiProvider retries a failed call once; extraction and persona generation retry
# that whole call once more on top.
QUESTION_ATTEMPTS, SETUP_ATTEMPTS = 2, 4
SHUFFLE_SEED = "v2.0-m7-j4"
OUT_DIR = ROOT / "attribution-judging"
SUMMARY = ROOT / "evals" / "v2.0-m7-attribution-probe.json"


@dataclass(frozen=True)
class Document:
    path: Path
    defense_type: DefenseType


DOCUMENTS = [
    Document(ROOT / "sample3.pdf", DefenseType.THESIS),
    Document(ROOT / "Group2_Library Management System for DAZSMA Documentation (1).pdf", DefenseType.CAPSTONE),
]

# A specific study: "Reyes et al." or "(Reyes, 2021)", "(Reyes & Cruz, 2021a)".
_CITATION = re.compile(r"\bet al\.|\([A-Z][^()]{0,80}?,\s*(?:19|20)\d{2}[a-z]?\)")
_NEXT_CHAPTER = re.compile(r"(?i)^chapter\s+(?:4|iv|four)\b")


def evenly_spaced(candidates: list[int], n: int) -> list[int]:
    """n of `candidates` (ascending) at even spacing, first and last included."""
    if len(candidates) < n:
        raise SystemExit(f"STOP: only {len(candidates)} candidates for {n} items")
    if n == 1:
        return [candidates[0]]
    return [candidates[round(i * (len(candidates) - 1) / (n - 1))] for i in range(n)]


def chapter_three_pages(path: Path, filtered: FilteredChunks) -> range:
    """0-based pages from the Chapter 3 heading (the review's end) to the next chapter
    heading, or to the reference list, or to the end of the document."""
    assert filtered.literature_review is not None
    start = filtered.literature_review.end[0]
    with fitz.open(str(path)) as pdf:
        texts = [page.get_text("text") for page in pdf]
    end = filtered.references.start[0] if filtered.references else len(texts)
    for index in range(start + 1, end):
        if any(_NEXT_CHAPTER.match(line.strip()) for line in texts[index].splitlines()):
            end = index
            break
    return range(start, end)


def candidates(path: Path, filtered: FilteredChunks) -> tuple[list[int], list[int]]:
    """(literature-review chunks citing a study, the control's own Chapter 3 chunks)."""
    review = [i for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))
              if a == "literature_review" and _CITATION.search(c.text)]
    pages = chapter_three_pages(path, filtered)
    own = [i for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))
           if a is None and c.page_start in pages and c.page_end in pages]
    return review, own


def review_counts(available: list[int]) -> list[int]:
    """How many literature-review chunks each document gives: REVIEW_ITEMS_PER_DOCUMENT
    where it has them, any shortfall taken from the first document with spare."""
    counts = [min(REVIEW_ITEMS_PER_DOCUMENT, n) for n in available]
    while sum(counts) < REVIEW_ITEMS:
        spare = [i for i, n in enumerate(available) if counts[i] < n]
        if not spare:
            raise SystemExit(f"STOP: only {sum(available)} literature-review candidates for {REVIEW_ITEMS} items")
        counts[spare[0]] += 1
    return counts


class _NoEncoder:
    """The fixed retriever never embeds; this fails loudly if anything tries."""

    def encode(self, texts):
        raise AssertionError("retrieval is bypassed in the probe")


def fixed(index: int):
    def retriever(query, chunks: list[Chunk], top_k=1, *, embedding_model, exclude_indices=frozenset()):
        return [(index, chunks[index])]

    return retriever


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.gemini_model != MODEL:
        raise SystemExit(f"STOP: the defense model is {settings.gemini_model}, not {MODEL}")
    calls = CallCounter()
    provider = gemini_providers_factory(settings)(calls).defense
    last_call = [0.0]

    def paced(fn, attempts: int = QUESTION_ATTEMPTS):
        """Runs one call 9 s after the last, if the budget allows its worst case of attempts."""
        if calls.total + attempts > args.max_calls:
            raise SystemExit(f"STOP: the next call could take the total past --max-calls {args.max_calls} "
                             f"({calls.total} made)")
        time.sleep(max(0.0, last_call[0] + PACE_S - time.monotonic()))
        try:
            return fn()
        finally:
            last_call[0] = time.monotonic()

    parsed = [parse_pdf(document.path) for document in DOCUMENTS]
    pools = [candidates(document.path, filtered) for document, filtered in zip(DOCUMENTS, parsed)]
    counts = review_counts([len(review) for review, _ in pools])

    OUT_DIR.mkdir(exist_ok=True)
    items, summary_docs = [], []
    generations = (OUT_DIR / "generations.jsonl").open("w", encoding="utf-8")
    for document, filtered, (review_pool, control_pool), count in zip(DOCUMENTS, parsed, pools, counts):
        metas = [ChunkMeta(i, c.text, c.page_start, c.page_end, a)
                 for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))]
        tagged = sum(a == "literature_review" for a in filtered.attributions)
        review, control = evenly_spaced(review_pool, count), evenly_spaced(control_pool, CONTROL_ITEMS)
        print(f"{document.path.name}: {len(review_pool)} of {tagged} tagged chunks cite a study; "
              f"review chunks {review}, control chunks {control}")
        extracted = paced(lambda: extract_document_profile([m.text for m in metas], provider), SETUP_ATTEMPTS)
        profile = DefenseProfile(
            defense_type=document.defense_type, domain=extracted.domain.strip() or "unspecified",
            topic=extracted.topic.strip() or document.path.stem, selected_archetypes=ARCHETYPES,
            document_id=f"m7-probe-{document.path.stem}",
        )
        panel, persona_fallback = paced(lambda: generate_panel(profile, ARCHETYPES, provider), SETUP_ATTEMPTS)
        # The session matrix only feeds retrieval, which the probe bypasses.
        matrix = np.zeros((len(metas), 1), dtype=np.float32)
        arms = {"before": engine_chunks(matrix, metas), "after": engine_chunks(matrix, metas, attribution_notes=True)}
        plan = [(arm, i) for i in review for arm in ("before", "after")] + [("control", i) for i in control]
        for archetype in ARCHETYPES:
            panelist = next(p for p in panel if p.archetype_key == archetype)
            for arm, index in plan:
                chunks = arms["after" if arm == "control" else arm]
                session = DefenseSession(profile=profile, panel=panel, difficulty_current=DIFFICULTY)
                turn = None
                for attempt in range(1, ITEM_TRIES + 1):
                    try:
                        turn = paced(lambda: _generate_question(
                            provider, session, chunks, _NoEncoder(), panelist, "", MODEL, retriever=fixed(index)
                        ))
                        break
                    except LLMProviderError as exc:
                        print(f"  {arm:7} chunk {index:3} {archetype}: try {attempt} failed ({type(exc).__name__}), "
                              f"calls={calls.total}")
                        if attempt == ITEM_TRIES:
                            raise
                        time.sleep(BUSY_WAIT_S)
                assert turn is not None
                original = metas[index].text
                grounded = is_grounded(turn.grounding_reference, original)
                note_only = not grounded and is_grounded(turn.grounding_reference, turn.chunk_text)
                item = {
                    "arm": arm, "document": document.path.name, "chunk": index, "archetype": archetype,
                    "pages": [metas[index].page_start + 1, metas[index].page_end + 1],
                    "grounded": grounded, "note_only_grounding": note_only,
                }
                items.append(item | {"question": turn.question, "passage": original})
                generations.write(json.dumps(item | {
                    "question": turn.question, "grounding_reference": turn.grounding_reference,
                    "prompt_saw_note": turn.chunk_text != original,
                }) + "\n")
                print(f"  {arm:7} chunk {index:3} {archetype:34} grounded={grounded} note_only={note_only} calls={calls.total}")
        summary_docs.append({
            "file": document.path.name, "defense_type": document.defense_type.value,
            "persona_fallback": persona_fallback,
            "tagged_chunks": tagged,
            "tagged_chunks_citing_a_study": len(review_pool),
            "literature_review_pages": [filtered.literature_review.start[0] + 1, filtered.literature_review.end[0] + 1],
            "review_chunks": [{"chunk": i, "pages": [metas[i].page_start + 1, metas[i].page_end + 1]} for i in review],
            "control_chunks": [{"chunk": i, "pages": [metas[i].page_start + 1, metas[i].page_end + 1]} for i in control],
        })
    generations.close()

    random.Random(SHUFFLE_SEED).shuffle(items)
    write_sheet(items)
    key = {f"{n:02d}": {k: v for k, v in item.items() if k not in ("question", "passage")}
           for n, item in enumerate(items, start=1)}
    (OUT_DIR / "key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")

    per_arm = {}
    for arm in ("before", "after", "control"):
        arm_items = [i for i in items if i["arm"] == arm]
        per_arm[arm] = {
            "items": len(arm_items),
            "grounded": sum(i["grounded"] for i in arm_items),
            "note_only_grounding": sum(i["note_only_grounding"] for i in arm_items),
        }
    summary = {
        "milestone": "v2.0 M7 J4", "model": MODEL, "difficulty": DIFFICULTY, "archetypes": ARCHETYPES,
        "calls": calls.total, "calls_by_stage": calls.by_stage, "per_arm": per_arm, "documents": summary_docs,
    }
    SUMMARY.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


SHEET_HEADER = """# v2.0 M7 attribution judging sheet

Each item is one generated question and the passage it was generated from.
The items are shuffled and carry no arm labels.
For each item, type one label after `Label:`:

- `M`: the question treats work the candidate cites as the candidate's own.
- `C`: the question treats the candidate's own work as cited work.
- `OK`: neither.
- `?`: unsure.

Then run `backend\\.venv\\Scripts\\python scripts\\score_attribution_judging.py` from the repo root.
"""


def write_sheet(items: list[dict]) -> None:
    parts = [SHEET_HEADER]
    for n, item in enumerate(items, start=1):
        quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in item["passage"].splitlines())
        parts.append(
            f"\n---\n\n## Item {n:02d}\n\n"
            f"- Document: {item['document']}\n"
            f"- Panelist: {ARCHETYPE_CONFIG[item['archetype']]['archetype_title']}\n"
            f"- PDF pages: {item['pages'][0]}-{item['pages'][1]}\n\n"
            f"**Question:** {item['question']}\n\n"
            f"**Passage:**\n\n{quoted}\n\n"
            f"Label:\n"
        )
    (OUT_DIR / "sheet.md").write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
