"""v2.0 M7 J4: the blinded before/after probe for literature-review attribution notes.

Usage (repo root, backend/.venv; GEMINI_API_KEY via backend.settings):

    python scripts/probe_attribution.py --max-calls 150

On sample3 and DAZSMA, through the production ingest and question path:
- 10 literature-review chunks (tagged by J2 as revised, so each cites a study),
  evenly spaced over a document's tagged chunks. Only documents with at least 5
  tagged chunks give items, 5 each, or all 10 when only one qualifies (Sean's
  decision of 2026-10-27; with J2 revised, DAZSMA has none). sample3's pages 16-18,
  the passage behind M7's live defect, is always one of them (FORCED_REVIEW_PAGES);
- per document, 3 of the candidate's own chunks from Chapter 3 (untagged, from the
  Chapter 3 heading to the next chapter heading, or to the reference list);
each asked by the Technical Implementation Reviewer and the Methodology Expert at
difficulty 3. Literature-review items run twice, notes off (before) and on (after);
control items once, notes on: 20 + 20 + 12 = 52 question calls, plus one extraction
and one persona call per document, as a real defense makes them.

The chunk is fixed through `_generate_question`'s `retriever` (M6 H3), so the
template, model, settings and grounding check are production's and only retrieval
is bypassed. Every Gemini call goes through CachedProvider:
- a response whose full prompt (and response model) was answered before, in any run,
  comes from attribution-judging/cache.jsonl instead of a new call;
- real calls start 9 s apart; GeminiProvider retries a call once, so at most two
  attempts per 9 s, under 15 a minute;
- a call that fails (503 overload, 504 timeout) is tried again after 30, 60, 120 and
  240 s; a 429 (real quota exhaustion) stops the run;
- attribution-judging/ledger.json counts every attempt across runs, and no call is
  made that could take the total past --max-calls.
A failed run can be started again: cached prompts cost nothing, and the persona is
cached too, so the question prompts come out the same.

Writes, all under attribution-judging/ (gitignored, they carry passage text):
- sheet.md: the shuffled items with no arm labels, a blank label each;
- key.json: item -> arm, document, chunk, grounding;
- generations.jsonl: every generation of this run; cache.jsonl; ledger.json.
And evals/v2.0-m7-attribution-probe.json: counts and chunk choices, no text.
Score the labels with scripts/score_attribution_judging.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Type, TypeVar

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402  PyMuPDF
import numpy as np  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from academic_defense_simulator.document_profile import extract_document_profile  # noqa: E402
from academic_defense_simulator.engine import _generate_question  # noqa: E402
from academic_defense_simulator.grounding import is_grounded  # noqa: E402
from academic_defense_simulator.llm.provider import (  # noqa: E402
    CallCounter,
    LLMProvider,
    LLMProviderError,
    LLMQuotaExhaustedError,
)
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType  # noqa: E402
from academic_defense_simulator.models.session import DefenseSession  # noqa: E402
from academic_defense_simulator.panel import generate_panel  # noqa: E402
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG  # noqa: E402
from academic_defense_simulator.rag.retrieval import Chunk  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.engine_adapter import engine_chunks  # noqa: E402
from backend.page_filter import FilteredChunks, front_matter_boundary  # noqa: E402
from backend.pdf_ingest import parse_pdf  # noqa: E402
from backend.sessions import ChunkMeta  # noqa: E402
from backend.settings import Settings  # noqa: E402

T = TypeVar("T", bound=BaseModel)

MODEL = "gemini-3.1-flash-lite"
ARCHETYPES = ["technical_implementation_reviewer", "methodology_expert"]
DIFFICULTY = 3
REVIEW_ITEMS, REVIEW_ITEMS_PER_DOCUMENT, CONTROL_ITEMS = 10, 5, 3
# Sean's deliberate deviation (2026-10-27): the probe must include the case that
# motivated M7, sample3's PDF pages 16-18 (the live check's question 1). That chunk
# replaces the evenly spaced pick nearest to it.
FORCED_REVIEW_PAGES = {"sample3.pdf": (16, 18)}
PACE_S = 9.0
ATTEMPTS_PER_CALL = 2  # GeminiProvider retries a failed call once
BACKOFF_S = (30.0, 60.0, 120.0, 240.0)  # waits before tries 2-5 of a failed call
SHUFFLE_SEED = "v2.0-m7-j4"
OUT_DIR = ROOT / "attribution-judging"
CACHE = OUT_DIR / "cache.jsonl"
LEDGER = OUT_DIR / "ledger.json"
SUMMARY = ROOT / "evals" / "v2.0-m7-attribution-probe.json"


@dataclass(frozen=True)
class Document:
    path: Path
    defense_type: DefenseType


DOCUMENTS = [
    Document(ROOT / "sample3.pdf", DefenseType.THESIS),
    Document(ROOT / "Group2_Library Management System for DAZSMA Documentation (1).pdf", DefenseType.CAPSTONE),
]

_CHAPTER_THREE = re.compile(r"(?i)^chapter\s+(?:3|iii|three)\b")
_NEXT_CHAPTER = re.compile(r"(?i)^chapter\s+(?:4|iv|four)\b")
# Dotted leaders, or a chapter title ending in a page number ("CHAPTER 3 METHODOLOGY 43").
_CONTENTS_LINE = re.compile(r"\.{3,}|…|^\S+\s+\S+\s+.*\s\d{1,3}$")


class CachedProvider(LLMProvider):
    """The defense provider behind the prompt-hash cache, the pacing, the backoff and
    the cross-run call ledger (see the module docstring)."""

    def __init__(self, inner: LLMProvider, calls: CallCounter, max_calls: int) -> None:
        self._inner, self._calls, self._max = inner, calls, max_calls
        self._cache = {}
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                self._cache[entry["key"]] = entry["response"]
        self._spent_before = json.loads(LEDGER.read_text(encoding="utf-8"))["calls"] if LEDGER.exists() else 0
        self._last_call = 0.0
        self.hits = 0

    @property
    def spent(self) -> int:
        """Attempts across every run, this one included."""
        return self._spent_before + self._calls.total

    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        key = _key(response_model.__name__, prompt)
        if key in self._cache:
            self.hits += 1
            return response_model.model_validate_json(self._cache[key])
        result = self._call(lambda: self._inner.generate_structured(prompt, response_model))
        self._store(key, result.model_dump_json())
        return result

    def generate_text(self, prompt: str) -> str:
        key = _key("text", prompt)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        result = self._call(lambda: self._inner.generate_text(prompt))
        self._store(key, result)
        return result

    def _call(self, fn: Callable[[], T]) -> T:
        for attempt in range(len(BACKOFF_S) + 1):
            if self.spent + ATTEMPTS_PER_CALL > self._max:
                raise SystemExit(f"STOP: the next call could take the total past --max-calls {self._max} "
                                 f"({self.spent} made across runs)")
            time.sleep(max(0.0, self._last_call + PACE_S - time.monotonic()))
            try:
                return fn()
            except LLMQuotaExhaustedError:
                raise
            except LLMProviderError as exc:
                if attempt == len(BACKOFF_S):
                    raise
                print(f"    call failed ({type(exc).__name__}); waiting {BACKOFF_S[attempt]:.0f} s, "
                      f"{self.spent} calls made across runs")
                time.sleep(BACKOFF_S[attempt])
            finally:
                self._last_call = time.monotonic()
                LEDGER.write_text(json.dumps({"calls": self.spent}) + "\n", encoding="utf-8")
        raise AssertionError("unreachable")

    def _store(self, key: str, response: str) -> None:
        self._cache[key] = response
        with CACHE.open("a", encoding="utf-8") as cache:
            cache.write(json.dumps({"key": key, "response": response}) + "\n")


def _key(kind: str, prompt: str) -> str:
    return hashlib.sha256(f"{MODEL}\n{kind}\n{prompt}".encode()).hexdigest()


def evenly_spaced(candidates: list[int], n: int) -> list[int]:
    """n of `candidates` (ascending) at even spacing, first and last included."""
    if len(candidates) < n:
        raise SystemExit(f"STOP: only {len(candidates)} candidates for {n} items")
    if n == 0:
        return []
    if n == 1:
        return [candidates[0]]
    return [candidates[round(i * (len(candidates) - 1) / (n - 1))] for i in range(n)]


def chapter_three_pages(path: Path, filtered: FilteredChunks) -> range:
    """0-based pages from the first Chapter 3 heading after the front matter to the
    next chapter heading, or to the reference list, or to the end of the document."""
    with fitz.open(str(path)) as pdf:
        texts = [page.get_text("text") for page in pdf]
    boundary = front_matter_boundary(texts)
    if boundary is None:
        raise SystemExit(f"STOP: no Chapter 1 in {path.name}")

    def heads(index: int, pattern: re.Pattern[str]) -> bool:
        return any(pattern.match(line.strip()) and not _CONTENTS_LINE.search(line.strip())
                   for line in texts[index].splitlines())

    start = next((i for i in range(boundary, len(texts)) if heads(i, _CHAPTER_THREE)), None)
    if start is None:
        raise SystemExit(f"STOP: no Chapter 3 heading in {path.name}")
    end = filtered.references.start[0] if filtered.references else len(texts)
    end = next((i for i in range(start + 1, end) if heads(i, _NEXT_CHAPTER)), end)
    return range(start, end)


def candidates(path: Path, filtered: FilteredChunks) -> tuple[list[int], list[int]]:
    """(the literature-review chunks J2 tags whole, the control's own Chapter 3 chunks)."""
    review = [i for i, a in enumerate(filtered.attributions) if a == "literature_review"]
    pages = chapter_three_pages(path, filtered)
    own = [i for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))
           if a is None and c.page_start in pages and c.page_end in pages]
    return review, own


def review_counts(available: list[int]) -> list[int]:
    """Literature-review items per document: only documents with at least
    REVIEW_ITEMS_PER_DOCUMENT tagged chunks give any, splitting REVIEW_ITEMS evenly
    (the first takes any remainder): 5 and 5, or 10 when only one qualifies."""
    eligible = [i for i, n in enumerate(available) if n >= REVIEW_ITEMS_PER_DOCUMENT]
    if not eligible:
        raise SystemExit(f"STOP: no document has {REVIEW_ITEMS_PER_DOCUMENT} tagged literature-review chunks")
    counts = [0] * len(available)
    for position, index in enumerate(eligible):
        counts[index] = REVIEW_ITEMS // len(eligible) + (REVIEW_ITEMS % len(eligible) if position == 0 else 0)
    if any(counts[i] > available[i] for i in eligible):
        raise SystemExit(f"STOP: {counts} items from {available} tagged chunks")
    return counts


def with_forced(picks: list[int], pool: list[int], forced: int | None) -> list[int]:
    """`picks` with `forced` in place of the pick nearest to it (on a tie, the earlier
    one), so the count stays the same. Unchanged if `forced` is None or already picked."""
    if forced is None or not picks or forced in picks:
        return picks
    if forced not in pool:
        raise SystemExit(f"STOP: the forced chunk {forced} isn't a tagged literature-review chunk")
    nearest = min(picks, key=lambda i: (abs(i - forced), i))
    return sorted([i for i in picks if i != nearest] + [forced])


def forced_chunk(document: Document, filtered: FilteredChunks) -> int | None:
    """The chunk at FORCED_REVIEW_PAGES for this document (1-based pages), if any."""
    pages = FORCED_REVIEW_PAGES.get(document.path.name)
    if pages is None:
        return None
    matches = [i for i, c in enumerate(filtered.chunks) if (c.page_start + 1, c.page_end + 1) == pages]
    if len(matches) != 1:
        raise SystemExit(f"STOP: {len(matches)} chunks span pages {pages[0]}-{pages[1]} of {document.path.name}")
    return matches[0]


class _NoEncoder:
    """The fixed retriever never embeds; this fails loudly if anything tries."""

    def encode(self, texts):
        raise AssertionError("retrieval is bypassed in the probe")


def fixed(index: int):
    def retriever(query, chunks: list[Chunk], top_k=1, *, embedding_model, exclude_indices=frozenset()):
        return [(index, chunks[index])]

    return retriever


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-calls", type=int, required=True, help="the cap on attempts across every run")
    args = parser.parse_args()
    settings = Settings()
    if settings.gemini_model != MODEL:
        raise SystemExit(f"STOP: the defense model is {settings.gemini_model}, not {MODEL}")
    OUT_DIR.mkdir(exist_ok=True)
    calls = CallCounter()
    provider = CachedProvider(gemini_providers_factory(settings)(calls).defense, calls, args.max_calls)
    print(f"{provider.spent} calls made in earlier runs; cap {args.max_calls}")

    parsed = [parse_pdf(document.path) for document in DOCUMENTS]
    pools = [candidates(document.path, filtered) for document, filtered in zip(DOCUMENTS, parsed)]
    counts = review_counts([len(review) for review, _ in pools])

    items, summary_docs = [], []
    generations = (OUT_DIR / "generations.jsonl").open("w", encoding="utf-8")
    for document, filtered, (review_pool, control_pool), count in zip(DOCUMENTS, parsed, pools, counts):
        metas = [ChunkMeta(i, c.text, c.page_start, c.page_end, a)
                 for i, (c, a) in enumerate(zip(filtered.chunks, filtered.attributions))]
        review = with_forced(evenly_spaced(review_pool, count), review_pool, forced_chunk(document, filtered))
        control = evenly_spaced(control_pool, CONTROL_ITEMS)
        print(f"{document.path.name}: {len(review_pool)} tagged chunks; review chunks {review}, control chunks {control}")
        extracted = extract_document_profile([m.text for m in metas], provider)
        profile = DefenseProfile(
            defense_type=document.defense_type, domain=extracted.domain.strip() or "unspecified",
            topic=extracted.topic.strip() or document.path.stem, selected_archetypes=ARCHETYPES,
            document_id=f"m7-probe-{document.path.stem}",
        )
        panel, persona_fallback = generate_panel(profile, ARCHETYPES, provider)
        # The session matrix only feeds retrieval, which the probe bypasses.
        matrix = np.zeros((len(metas), 1), dtype=np.float32)
        arms = {"before": engine_chunks(matrix, metas), "after": engine_chunks(matrix, metas, attribution_notes=True)}
        plan = [(arm, i) for i in review for arm in ("before", "after")] + [("control", i) for i in control]
        for archetype in ARCHETYPES:
            panelist = next(p for p in panel if p.archetype_key == archetype)
            for arm, index in plan:
                chunks = arms["after" if arm == "control" else arm]
                session = DefenseSession(profile=profile, panel=panel, difficulty_current=DIFFICULTY)
                turn = _generate_question(
                    provider, session, chunks, _NoEncoder(), panelist, "", MODEL, retriever=fixed(index)
                )
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
                print(f"  {arm:7} chunk {index:3} {archetype:34} grounded={grounded} note_only={note_only} "
                      f"calls={provider.spent} cache_hits={provider.hits}")
        review_range = filtered.literature_review
        summary_docs.append({
            "file": document.path.name, "defense_type": document.defense_type.value,
            "persona_fallback": persona_fallback,
            "tagged_chunks": len(review_pool),
            "literature_review_pages": [review_range.start[0] + 1, review_range.end[0] + 1] if review_range else None,
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
        "calls_this_run": calls.total, "calls_across_runs": provider.spent, "cache_hits_this_run": provider.hits,
        "per_arm": per_arm, "documents": summary_docs,
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
