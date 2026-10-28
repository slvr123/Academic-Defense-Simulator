"""v2.0 M4 retrieval eval: checks A-E on the production retrieval stack, offline.

Usage (from the repo root, in backend/.venv, which has the ONNX runtime):

    python scripts/run_retrieval_eval.py               # production ingest (M6: + reference list)
    python scripts/run_retrieval_eval.py --chunker m5  # M5's chunker (navigation pages only)
    python scripts/run_retrieval_eval.py --chunker v1  # v1's chunker, as M4 measured it
    python scripts/run_retrieval_eval.py --lit-review-access literature  # M9 L2 routing (or `none`)

Chunks come from the backend's own `parse_pdf`, embeddings from `OnnxEmbedder`
(backend/model), and every simulated session is also run through v1's
`retrieve()` on `engine_chunks`, the exact objects a live defense uses. No model
API is called. Check E's validation against the M3 live run comes first and
stops the run on a mismatch. That run predates M5, so only `--chunker v1` can
reproduce it; on the production chunker the replay is still checked against
`retrieve()` turn by turn.

Writes evals/v2.0-m6-retrieval-results.json (production), or rewrites the M5 or M4
file (`--chunker m5`, `--chunker v1`), which must come out byte-identical: ranks, scores, page numbers, chunk
indices and hashes, never passage text. See docs/v2.0-m4-retrieval-eval-decisions.md.

With `--lit-review-access` (M9 L4) every check runs as a live defense retrieves under
L2's routing: an archetype barred from the literature review scores its tagged chunks
at -inf, so its ranking, near-tie band and displacement rank cover only the chunks it
may draw, and every simulated session is still checked turn by turn against
production's retriever, now wrapped in `backend.routing.routed`. A document without
tagged chunks comes out exactly as before. The run writes
evals/v2.0-m9-retrieval-results.json, with a `literature_routing` section of
tagged-chunk picks per archetype before and after routing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY, PANEL_COMPOSITION  # noqa: E402
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG  # noqa: E402
from academic_defense_simulator.rag.retrieval import retrieve  # noqa: E402
from backend import embedder as backend_embedder  # noqa: E402
from backend.engine_adapter import RecordingEncoder, engine_chunks  # noqa: E402
from academic_defense_simulator.rag.chunking import (  # noqa: E402
    DocumentIngestionError,
    InsufficientTextError,
    PageChunk,
    chunk_pdf_with_pages,
)
from backend.page_filter import (  # noqa: E402
    chunk_pdf_filtered,
    front_matter_boundary,
    reference_range,
    removable_pages,
    trim_references,
)
from backend.pdf_ingest import IngestFailure, parse_pdf  # noqa: E402
from backend.retrieval_sampling import NearTieRetriever, turn_seed  # noqa: E402
from backend.routing import blocked_chunks, routed  # noqa: E402
from backend.schemas import MAX_DOMAIN_PANELISTS  # noqa: E402
from backend.sessions import ChunkMeta  # noqa: E402
from evals import retrieval_checks as rc  # noqa: E402

MODEL_DIR = ROOT / "backend" / "model"
OUTPUTS = {
    "production": ROOT / "evals" / "v2.0-m6-retrieval-results.json",
    "m5": ROOT / "evals" / "v2.0-m5-retrieval-results.json",
    "v1": ROOT / "evals" / "v2.0-m4-retrieval-results.json",
}
MILESTONES = {"production": "v2.0 M6", "m5": "v2.0 M5", "v1": "v2.0 M4"}
ROUTED_OUTPUT = ROOT / "evals" / "v2.0-m9-retrieval-results.json"
# A removed reference line this long can't turn up in a kept chunk by coincidence.
REFERENCE_LINE_MIN_CHARS = 20
SEPTEMBER_CASES = "feat/retrieval-sources:evals/retrieval_cases.json"
# Not in F4's list (repo root or the September cases), so reported but kept out of every verdict.
SUPPLEMENTARY = [ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf"]
SESSION_TURNS = 6  # F5 Check E; the engine caps a session at one new topic per domain panelist
SEEDED_SESSIONS = 20  # M6 H3: synthetic session ids per defense type for the seeded Check E

# M3 live run, docs/v2.0-m3-results.md: DAZSMA, capstone, recommended panel. Sources are
# 1-based pages and the score SourceTracker reported. Turn 4 is Devil's Advocate, who
# doesn't retrieve; the live run reused turn 2's chunk.
LIVE_DOCUMENT_SHA = "b342cdf4bebecaf589a55e51779e1459cb844a4d7848f41fb49ae624432d1331"
LIVE_DEFENSE_TYPE = "capstone"
LIVE_NEW_TOPIC_SOURCES = [(28, 28, 0.4966), (121, 123, 0.3899), (172, 174, 0.2708)]
LIVE_DA_SOURCE = (121, 123)

RETRIEVING_ARCHETYPES = [k for k in ARCHETYPE_CONFIG if k != DEVILS_ADVOCATE_KEY]


@dataclass
class Document:
    path: Path
    sha256: str
    in_verdict: bool
    page_texts: list[str]
    chunks: list[ChunkMeta]
    matrix: np.ndarray
    token_counts: list[int]
    access: str | None = None  # M9 L2's lit_review_access, None for no routing

    @property
    def chunk_pages(self) -> list[tuple[int, int]]:
        return [(c.page_start, c.page_end) for c in self.chunks]

    def pages_1based(self, chunk: int) -> list[int]:
        meta = self.chunks[chunk]
        return [meta.page_start + 1, meta.page_end + 1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def september_hashes() -> dict[str, str]:
    raw = subprocess.run(["git", "show", SEPTEMBER_CASES], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {d["file"]: d["sha256"] for d in json.loads(raw)["documents"]}


def candidate_paths(september: dict[str, str]) -> list[Path]:
    paths = sorted(ROOT.glob("*.pdf")) + [ROOT / name for name in september]
    return list(dict.fromkeys(paths))


def _parse_with(chunker, path: Path) -> list[PageChunk]:
    """`parse_pdf` with an earlier chunker, and the same IngestFailure mapping."""
    try:
        chunks = chunker(path)
    except InsufficientTextError as exc:
        raise IngestFailure("scanned", code="scanned_pdf") from exc
    except DocumentIngestionError as exc:
        raise IngestFailure(str(exc)) from exc
    if not chunks:
        raise IngestFailure("scanned", code="scanned_pdf")
    return chunks


PARSERS = {
    "production": lambda path: parse_pdf(path).chunks,
    "m5": lambda path: _parse_with(lambda p: chunk_pdf_filtered(p, trim_reference_list=False).chunks, path),
    "v1": lambda path: _parse_with(chunk_pdf_with_pages, path),
}


def load_document(path: Path, in_verdict: bool, embedder, counter, parse, access: str | None = None) -> Document:
    """Ingest as IngestQueue._ingest does it: `parse` (production: backend.pdf_ingest.parse_pdf),
    then OnnxEmbedder.encode_one per chunk. With `access`, each chunk carries its M7 tag."""
    page_chunks = parse(path)
    tags = parse_pdf(path).attributions if access is not None else [None] * len(page_chunks)
    chunks = [
        ChunkMeta(chunk_id=i, text=c.text, page_start=c.page_start, page_end=c.page_end, attribution=tag)
        for i, (c, tag) in enumerate(zip(page_chunks, tags, strict=True))
    ]
    matrix = np.stack([embedder.encode_one(c.text) for c in chunks]).astype(np.float32)
    with fitz.open(str(path)) as pdf:
        page_texts = [page.get_text("text") for page in pdf]
    return Document(
        path=path,
        sha256=sha256(path),
        in_verdict=in_verdict,
        page_texts=page_texts,
        chunks=chunks,
        matrix=matrix,
        token_counts=[len(counter.encode(c.text).ids) for c in chunks],
        access=access,
    )


def recommended_panel(composition_key: str) -> list[str]:
    """The frontend's "Use recommended panel": the roster's first max_domain_panelists."""
    return PANEL_COMPOSITION[composition_key][:MAX_DOMAIN_PANELISTS]


def production_session(doc: Document, panel: list[str], embedder, session_id: str | None = None) -> list[tuple[int, float]]:
    """The same turns through the retriever a live defense uses, on the engine's chunk
    objects: v1's retrieve() (top-1), or with `session_id` M6's NearTieRetriever seeded
    as backend/defense.py seeds it. (chunk index, score as SourceTracker computes it)."""
    chunks = engine_chunks(doc.matrix, doc.chunks)
    encoder = RecordingEncoder(embedder)
    used: set[int] = set()
    turns = []
    for archetype in panel[:SESSION_TURNS]:
        query = ARCHETYPE_CONFIG[archetype]["archetype_focus"]
        retriever = retrieve if session_id is None else NearTieRetriever(turn_seed(session_id, len(turns) + 1))
        if doc.access is not None:
            retriever = routed(retriever, doc.chunks, archetype, doc.access)
        index, _ = retriever(query, chunks, top_k=1, embedding_model=encoder, exclude_indices=frozenset(used))[0]
        used.add(index)
        turns.append((index, float(doc.matrix[index] @ encoder.take_query())))
    return turns


def simulate(doc: Document, rankings: dict[str, list[int]], panel: list[str], embedder) -> list[rc.ReplayTurn]:
    """Pure replay, checked turn by turn against the production path."""
    replay = rc.replay_new_topic_turns([(a, rankings[a]) for a in panel], SESSION_TURNS)
    production = production_session(doc, panel, embedder)
    if [t.chunk for t in replay] != [index for index, _ in production]:
        raise SystemExit(f"HALT: replay disagrees with production retrieve() on {doc.path.name}, panel {panel}")
    return replay


def validate_against_live_run(docs: list[Document], rankings: dict[str, dict[str, list[int]]], embedder) -> dict:
    doc = next((d for d in docs if d.sha256 == LIVE_DOCUMENT_SHA), None)
    if doc is None:
        raise SystemExit("HALT: the M3 live-run document (DAZSMA) isn't available")
    panel = recommended_panel(LIVE_DEFENSE_TYPE)
    production = production_session(doc, panel, embedder)
    simulated = simulate(doc, rankings[doc.sha256], panel, embedder)
    rows = []
    for turn, (index, score), live in zip(simulated, production, LIVE_NEW_TOPIC_SOURCES):
        pages = doc.pages_1based(turn.chunk)
        rows.append({
            "turn": turn.turn,
            "archetype": turn.archetype,
            "simulated_pages": pages,
            "simulated_score": round(score, 4),
            "live_pages": [live[0], live[1]],
            "live_score": live[2],
            "match": pages == [live[0], live[1]] and round(score, 4) == live[2],
        })
    da_pages = [list(LIVE_DA_SOURCE)]
    da_reuses_a_simulated_chunk = any(doc.pages_1based(t.chunk) == da_pages[0] for t in simulated)
    result = {
        "document": doc.path.name,
        "defense_type": LIVE_DEFENSE_TYPE,
        "panel": panel,
        "turns": rows,
        "turn_4_devils_advocate": {"live_pages": da_pages[0], "reuses_a_simulated_chunk": da_reuses_a_simulated_chunk},
    }
    if not all(r["match"] for r in rows) or not da_reuses_a_simulated_chunk:
        print(json.dumps(result, indent=2))
        raise SystemExit("HALT: Check E validation mismatch against the M3 live run")
    return result


def check_a(doc: Document, rankings: dict[str, list[int]]) -> dict:
    pairs = rc.pair_overlaps(rankings)
    shared = [p.shared_top_k for p in pairs]
    median = statistics.median(shared)
    return {
        "pairs": [asdict(p) for p in pairs],
        "median_shared_top5": median,
        "mean_shared_top5": statistics.fmean(shared),
        "same_top1_pairs": sum(p.same_top1 for p in pairs),
        "pair_count": len(pairs),
        "random_baseline_shared_top5": rc.random_overlap_baseline(len(doc.chunks)),
        "distinct_top1_chunks": len({r[0] for r in rankings.values()}),
        "distinct_top5_chunks": len({i for r in rankings.values() for i in r[:5]}),
        "verdict": rc.overlap_verdict(median),
    }


def check_b(doc: Document, scores: dict[str, np.ndarray]) -> dict:
    spreads = {a: rc.score_spread(s) for a, s in scores.items()}
    return {
        "per_archetype": {
            a: {"top5_scores": [round(x, 4) for x in s.top_scores], "gap_1_2": round(s.gap_1_2, 4), "near_tie": s.near_tie}
            for a, s in spreads.items()
        },
        "near_tie_rate": rc.near_tie_rate(list(spreads.values())),
        "gap_distribution": rc.distribution([s.gap_1_2 for s in spreads.values()]),
    }


def check_c(doc: Document, rankings: dict[str, list[int]], session_picks: list[int]) -> dict:
    navigation = {i for i, text in enumerate(doc.page_texts) if rc.is_navigation_page(text)}
    boundary = rc.front_matter_boundary(doc.page_texts)
    result: dict = {
        "boundary_page": None if boundary is None else boundary + 1,
        "navigation_pages": sorted(p + 1 for p in navigation),
    }
    if boundary is None:
        result["halted"] = "no Chapter 1 / Introduction heading found, so front matter is undefined"
        return result
    top1 = [r[0] for r in rankings.values()]
    summary = rc.front_matter_summary(doc.chunk_pages, boundary, navigation, top1)
    sessions = rc.front_matter_summary(doc.chunk_pages, boundary, navigation, session_picks)
    result.update({
        "front_matter_chunks": sum(start < boundary for start, _ in doc.chunk_pages),
        "base_rate": summary.base_rate,
        "top1_pick_rate": summary.pick_rate,
        "ratio": summary.ratio,
        "top1_navigation_picks": [{"chunk": i, "pages": doc.pages_1based(i)} for i in summary.navigation_picks],
        "session_pick_rate": sessions.pick_rate,
        "session_navigation_picks": [{"chunk": i, "pages": doc.pages_1based(i)} for i in sessions.navigation_picks],
    })
    if summary.ratio is None:
        result["halted"] = "the base rate is 0, so the ratio is undefined"
    else:
        result["over_retrieved"] = summary.ratio > 1.5 or bool(summary.navigation_picks)
    return result


def check_d(doc: Document) -> dict:
    return asdict(rc.embedding_coverage(doc.token_counts))


def check_e(doc: Document, rankings: dict[str, list[int]], queries: dict[str, np.ndarray], embedder) -> dict:
    sessions = {}
    for key in PANEL_COMPOSITION:
        panel = recommended_panel(key)
        turns = simulate(doc, rankings, panel, embedder)
        sessions[key] = [
            {**asdict(t), "pages": doc.pages_1based(t.chunk), "score": round(float(doc.matrix[t.chunk] @ queries[t.archetype]), 4)}
            for t in turns
        ]
    ranks = [t["displacement_rank"] for turns in sessions.values() for t in turns]
    return {
        "sessions": sessions,
        "max_displacement_rank": max(ranks),
        "turns_displaced": sum(r > 1 for r in ranks),
        "turns_over_5": sum(r > 5 for r in ranks),
        "turn_count": len(ranks),
    }


def check_e_seeded(doc: Document, scores: dict[str, np.ndarray], embedder, avoid: set[int]) -> dict:
    """H3's seeded replay: SEEDED_SESSIONS synthetic defenses per recommended panel, each
    checked against production's NearTieRetriever and replayed a second time to show
    it reproduces. `avoid` are chunks carrying navigation or reference text."""
    per_type = {}
    all_turns = []
    for key in PANEL_COMPOSITION:
        panel = recommended_panel(key)
        sequences = []
        for n in range(SEEDED_SESSIONS):
            session_id = f"m6-eval-{n}"
            turns = rc.replay_seeded_turns([(a, scores[a]) for a in panel], session_id, SESSION_TURNS)
            if turns != rc.replay_seeded_turns([(a, scores[a]) for a in panel], session_id, SESSION_TURNS):
                raise SystemExit(f"HALT: seeded replay isn't reproducible on {doc.path.name}, {key}")
            production = [index for index, _ in production_session(doc, panel, embedder, session_id)]
            if [t.chunk for t in turns] != production:
                raise SystemExit(f"HALT: seeded replay disagrees with NearTieRetriever on {doc.path.name}, {key}")
            sequences.append(tuple(t.chunk for t in turns))
            all_turns += turns
        per_type[key] = {
            "distinct_sessions": len(set(sequences)),
            "distinct_chunks_per_turn": [len({seq[i] for seq in sequences}) for i in range(len(sequences[0]))],
        }
    # How far below the best still-available chunk each pick scored: under NEAR_TIE_GAP by construction.
    return {
        "sessions_per_type": SEEDED_SESSIONS,
        "per_type": per_type,
        "max_displacement_rank": max(t.displacement_rank for t in all_turns),
        "turns_over_5": sum(t.displacement_rank > 5 for t in all_turns),
        "turn_count": len(all_turns),
        "turns_on_avoided_chunks": sum(t.chunk in avoid for t in all_turns),
    }


def max_score_drop(doc: Document, scores: dict[str, np.ndarray]) -> float:
    """The largest gap between a seeded pick and the best chunk it could have had,
    over every seeded session: the relevance the sampling gave up."""
    drop = 0.0
    for key in PANEL_COMPOSITION:
        panel = recommended_panel(key)
        for n in range(SEEDED_SESSIONS):
            used: set[int] = set()
            for t in rc.replay_seeded_turns([(a, scores[a]) for a in panel], f"m6-eval-{n}", SESSION_TURNS):
                s = scores[t.archetype]
                best = max(float(s[i]) for i in range(len(s)) if i not in used)
                drop = max(drop, best - float(s[t.chunk]))
                used.add(t.chunk)
    return drop


def longest_string(value) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return max([longest_string(k) for k in value] + [longest_string(v) for v in value.values()], default=0)
    if isinstance(value, list):
        return max((longest_string(v) for v in value), default=0)
    return 0



def removal_report(doc: Document) -> dict:
    """G5 criterion 2: which pages the filter takes out, and whether any sits at or after the boundary."""
    boundary = front_matter_boundary(doc.page_texts)
    removed = sorted(removable_pages(doc.page_texts))
    return {
        "boundary_page": None if boundary is None else boundary + 1,
        "removed_pages": [p + 1 for p in removed],
        "removed_at_or_after_boundary": 0 if boundary is None else sum(p >= boundary for p in removed),
        "filter_applied": boundary is not None,
    }


def reference_report(doc: Document, top1_picks: list[int], session_picks: list[int]) -> dict:
    """H2: where H1's reference range is, whether anything outside it changes, and which
    picks carry its text. On the M5 chunker the same range is measured, so the before
    run counts the picks that H1 is meant to remove."""
    references = reference_range(doc.page_texts)
    if references is None:
        return {"filter_applied": False}
    trimmed = trim_references(doc.page_texts, references)
    start_page, start_line = references.start
    end_page, end_line = references.end or (len(doc.page_texts), 0)
    removed_lines = []
    outside_changes = 0
    for index, (before, after) in enumerate(zip(doc.page_texts, trimmed)):
        lines = before.splitlines(keepends=True)
        if index < start_page or index > end_page:
            outside_changes += before != after
            continue
        keep_before = "".join(lines[:start_line]) if index == start_page else ""
        keep_after = "".join(lines[end_line:]) if index == end_page else ""
        outside_changes += after != keep_before + keep_after  # anything kept besides the two allowed ends
        first = start_line if index == start_page else 0
        last = end_line if index == end_page else len(lines)
        removed_lines += [line.strip() for line in lines[first:last]]
    long_lines = [line for line in removed_lines if len(line) >= REFERENCE_LINE_MIN_CHARS]
    # Position and text both: body chunks can quote a reference line word for word (a
    # table's source citation), so only a chunk reaching the reference pages counts.
    carrying = {
        i for i, chunk in enumerate(doc.chunks)
        if start_page <= chunk.page_end and chunk.page_start <= end_page and any(line in chunk.text for line in long_lines)
    }
    return {
        "filter_applied": True,
        "start": {"page": start_page + 1, "line": start_line},
        "end": None if references.end is None else {"page": end_page + 1, "line": end_line},
        "removed_lines": len(removed_lines),
        "changes_outside_range": outside_changes,
        "chunks_with_reference_text": sorted(carrying),
        "top1_reference_picks": sum(i in carrying for i in top1_picks),
        "session_reference_turns": sum(i in carrying for i in session_picks),
    }


def seeded_report(doc: Document, scores: dict[str, np.ndarray], embedder) -> dict:
    navigation = {i for i in range(len(doc.chunks)) if any(
        rc.is_navigation_page(doc.page_texts[p]) for p in range(doc.chunks[i].page_start, doc.chunks[i].page_end + 1)
    ) and doc.chunks[i].page_start < (front_matter_boundary(doc.page_texts) or 0)}
    references = set(reference_report(doc, [], []).get("chunks_with_reference_text", []))
    report = check_e_seeded(doc, scores, embedder, navigation | references)
    report["max_score_drop"] = round(max_score_drop(doc, scores), 4)
    return report


def routed_scores(doc: Document, scores: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Each archetype's scores with the chunks it may not draw at -inf: last in its
    ranking, never in a near-tie band. Unchanged arrays when nothing is blocked."""
    out = {}
    for archetype, s in scores.items():
        blocked = blocked_chunks(doc.chunks, archetype, doc.access)
        if blocked:
            s = s.copy()
            s[sorted(blocked)] = -np.inf
        out[archetype] = s
    return out


def tagged_picks(scores: dict[str, np.ndarray], tagged: set[int], sessions: bool) -> dict[str, dict[str, int]]:
    """Per archetype: whether its top-1 is tagged, and its turns on tagged chunks over
    Check E's plain sessions and the seeded sessions (M6 H3's ids), when the document
    is long enough for Check E (`sessions`)."""
    out = {a: {"top1": int(rc.ranking(s)[0] in tagged), "plain_turns": 0, "seeded_turns": 0, "seeded_turns_total": 0}
           for a, s in scores.items()}
    rankings = {a: rc.ranking(s) for a, s in scores.items()}
    for key in PANEL_COMPOSITION if sessions else []:
        panel = recommended_panel(key)
        for t in rc.replay_new_topic_turns([(a, rankings[a]) for a in panel], SESSION_TURNS):
            out[t.archetype]["plain_turns"] += t.chunk in tagged
        for n in range(SEEDED_SESSIONS):
            for t in rc.replay_seeded_turns([(a, scores[a]) for a in panel], f"m6-eval-{n}", SESSION_TURNS):
                out[t.archetype]["seeded_turns"] += t.chunk in tagged
                out[t.archetype]["seeded_turns_total"] += 1
    return out


def routing_report(doc: Document, raw: dict[str, np.ndarray], routed_: dict[str, np.ndarray]) -> dict:
    """L4 criterion 1: tagged-chunk picks per archetype, before routing and after."""
    tagged = {i for i, c in enumerate(doc.chunks) if c.attribution is not None}
    sessions = len(doc.chunks) >= rc.OVERLAP_K
    before, after = tagged_picks(raw, tagged, sessions), tagged_picks(routed_, tagged, sessions)
    return {
        "access": doc.access,
        "tagged_chunks": sorted(tagged),
        "tagged_pages": [doc.pages_1based(i) for i in sorted(tagged)],
        "per_archetype": {a: {"before": before[a], "after": after[a]} for a in raw},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="v2.0 retrieval eval, checks A-E")
    parser.add_argument("--chunker", choices=sorted(PARSERS), default="production")
    parser.add_argument("--lit-review-access", choices=["literature", "none"], default=None,
                        help="M9 L2 routing (production chunker only)")
    args = parser.parse_args()
    if args.lit_review_access is not None and args.chunker != "production":
        raise SystemExit("--lit-review-access needs the production chunker")
    parse = PARSERS[args.chunker]
    if backend_embedder.MAX_SEQ_LENGTH != rc.MAX_SEQ_LENGTH:
        raise SystemExit("evals.retrieval_checks.MAX_SEQ_LENGTH is out of step with backend.embedder")
    embedder = backend_embedder.OnnxEmbedder.load(MODEL_DIR)
    from tokenizers import Tokenizer

    counter = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    counter.no_truncation()  # the embedder truncates at 256; counting needs the full length

    september = september_hashes()
    inventory, docs = [], []
    for path, in_verdict in [(p, True) for p in candidate_paths(september)] + [(p, False) for p in SUPPLEMENTARY]:
        entry = {"file": path.name, "in_verdict": in_verdict}
        if not path.exists():
            inventory.append({**entry, "usable": False, "reason": "not present locally"})
            continue
        entry["sha256"] = sha256(path)
        if path.name in september:
            entry["matches_september_hash"] = entry["sha256"] == september[path.name]
        try:
            doc = load_document(path, in_verdict, embedder, counter, parse, args.lit_review_access)
        except IngestFailure as exc:
            inventory.append({**entry, "usable": False, "reason": f"parse_pdf: {exc.code or 'error'}"})
            continue
        docs.append(doc)
        inventory.append({**entry, "usable": True, "pages": len(doc.page_texts), "chunks": len(doc.chunks)})
    verdict_docs = [d for d in docs if d.in_verdict]
    if len(verdict_docs) < 2:
        raise SystemExit(f"HALT: {len(verdict_docs)} usable documents; F4 needs at least 2")

    query_vectors = {
        a: embedder.encode_one(ARCHETYPE_CONFIG[a]["archetype_focus"]) for a in RETRIEVING_ARCHETYPES
    }
    raw_scores = {d.sha256: {a: d.matrix @ query_vectors[a] for a in RETRIEVING_ARCHETYPES} for d in docs}
    scores = {d.sha256: routed_scores(d, raw_scores[d.sha256]) for d in docs} if args.lit_review_access else raw_scores
    rankings = {d.sha256: {a: rc.ranking(s) for a, s in scores[d.sha256].items()} for d in docs}

    if args.chunker == "v1":
        validation = validate_against_live_run(docs, rankings, embedder)
    else:
        validation = {"skipped": "the M3 live run used v1's chunker; see --chunker v1"}

    per_document = []
    for doc in docs:
        ranked = rankings[doc.sha256]
        # F6 "none of the above": a document without OVERLAP_K chunks can't give any
        # archetype a top 5, so A, B and E halt for it; C and D still run.
        short = None if len(doc.chunks) >= rc.OVERLAP_K else {"halted": f"{len(doc.chunks)} chunks; checks A, B and E need {rc.OVERLAP_K}"}
        e = short or check_e(doc, ranked, query_vectors, embedder)
        session_picks = [t["chunk"] for turns in e.get("sessions", {}).values() for t in turns]
        per_document.append({
            "file": doc.path.name,
            "sha256": doc.sha256,
            "in_verdict": doc.in_verdict,
            "pages": len(doc.page_texts),
            "chunks": len(doc.chunks),
            "chunk_pages": [doc.pages_1based(i) for i in range(len(doc.chunks))],
            "rankings_top10": {a: r[:10] for a, r in ranked.items()},
            "check_a": short or check_a(doc, ranked),
            "check_b": short or check_b(doc, scores[doc.sha256]),
            "check_c": check_c(doc, ranked, session_picks),
            "check_d": check_d(doc),
            "check_e": e,
            # Only where the chunker had them, so the v1 and m5 runs stay byte-identical to M4's and M5's files.
            **({"page_filter": removal_report(doc)} if args.chunker != "v1" else {}),
            **({"reference_filter": reference_report(doc, [r[0] for r in ranked.values()], session_picks)} if args.chunker == "production" else {}),
            **({"check_e_seeded": seeded_report(doc, scores[doc.sha256], embedder)} if args.chunker == "production" and not short else {}),
        })
        if args.chunker == "m5":  # H2's before numbers, printed only: M5's file must not change
            print(f"before H1, {doc.path.name[:40]}: {reference_report(doc, [r[0] for r in ranked.values()], session_picks)}")

    counted = [d for d in per_document if d["in_verdict"]]
    ran = {k: [d for d in counted if "halted" not in d[k]] for k in ("check_a", "check_b", "check_c", "check_e")}
    pooled_median = statistics.median(p["shared_top_k"] for d in ran["check_a"] for p in d["check_a"]["pairs"])
    doc_verdicts = {d["file"]: d["check_a"]["verdict"] for d in ran["check_a"]}
    disagree = "differentiated" in doc_verdicts.values() and "collapsed" in doc_verdicts.values()
    pooled_ties = [s["near_tie"] for d in ran["check_b"] for s in d["check_b"]["per_archetype"].values()]
    near_tie = sum(pooled_ties) / len(pooled_ties)
    summary = {
        "halted": {d["file"]: [k for k in ("check_a", "check_b", "check_c", "check_e") if "halted" in d[k]] for d in counted},
        "check_a": {
            "documents": [d["file"] for d in ran["check_a"]],
            "pooled_median_shared_top5": pooled_median,
            "per_document": doc_verdicts,
            "verdict": "documents disagree" if disagree else rc.overlap_verdict(pooled_median),
        },
        "check_b": {
            "documents": [d["file"] for d in ran["check_b"]],
            "pooled_near_tie_rate": near_tie,
            "verdict": "top_k=1 among near-equals" if near_tie > 0.5 else "top_k=1 defensible",
        },
        "check_c": {d["file"]: d["check_c"]["over_retrieved"] for d in ran["check_c"]},
        "check_d": {
            d["file"]: {"over_limit": d["check_d"]["over_limit"], "chunks": d["check_d"]["chunks"], "mean_seen_share": round(d["check_d"]["mean_seen_share"], 3)}
            for d in counted
        },
        "check_e": {
            d["file"]: {"max_displacement_rank": d["check_e"]["max_displacement_rank"], "turns_over_5": d["check_e"]["turns_over_5"]}
            for d in ran["check_e"]
        },
    }
    output = {
        "milestone": MILESTONES[args.chunker],
        "embedder": "backend.embedder.OnnxEmbedder (backend/model/model.onnx)",
        "max_seq_length": backend_embedder.MAX_SEQ_LENGTH,
        "near_tie_gap": rc.NEAR_TIE_GAP,
        "overlap_k": rc.OVERLAP_K,
        "retrieving_archetypes": RETRIEVING_ARCHETYPES,
        "documents": inventory,
        "validation": validation,
        "summary": summary,
        "per_document": per_document,
    }
    if args.lit_review_access is not None:
        output["milestone"] = "v2.0 M9"
        output["literature_routing"] = {d.path.name: routing_report(d, raw_scores[d.sha256], scores[d.sha256]) for d in docs}
    longest = longest_string(output)
    if longest > 200:
        raise SystemExit(f"HALT: a string of {longest} characters would go into the results JSON")
    out_path = ROUTED_OUTPUT if args.lit_review_access is not None else OUTPUTS[args.chunker]
    out_path.write_text(json.dumps(output, indent=1) + "\n", encoding="utf-8")
    print_summary(output, longest)


def print_summary(output: dict, longest: int) -> None:
    print("documents:")
    for d in output["documents"]:
        print(f"  {d['file'][:60]:60} usable={d['usable']} in_verdict={d['in_verdict']} "
              + (f"pages={d['pages']} chunks={d['chunks']}" if d["usable"] else d["reason"])
              + (f" sept_hash_match={d['matches_september_hash']}" if "matches_september_hash" in d else ""))
    print("validation (M3 live run):")
    if "skipped" in output["validation"]:
        print(f"  skipped: {output['validation']['skipped']}")
    for t in output["validation"].get("turns", []):
        print(f"  turn {t['turn']} {t['archetype']:34} sim p{t['simulated_pages']} {t['simulated_score']}  live p{t['live_pages']} {t['live_score']}  match={t['match']}")
    if "turns" in output["validation"]:
        print(f"  turn 4 DA reuses a simulated chunk: {output['validation']['turn_4_devils_advocate']['reuses_a_simulated_chunk']}")
    for d in output["per_document"]:
        a, b, c, dd, e = (d[k] for k in ("check_a", "check_b", "check_c", "check_d", "check_e"))
        print(f"{d['file'][:60]} (in_verdict={d['in_verdict']}, {d['chunks']} chunks)")
        if "page_filter" in d:
            print(f"  filter {d['page_filter']}")
        if "reference_filter" in d:
            print(f"  references {d['reference_filter']}")
        if "check_e_seeded" in d:
            s = d["check_e_seeded"]
            print(f"  E seeded: {s['sessions_per_type']} sessions/type, max displacement={s['max_displacement_rank']} "
                  f"over5={s['turns_over_5']}/{s['turn_count']} avoided={s['turns_on_avoided_chunks']} max_score_drop={s['max_score_drop']}")
            for key, v in s["per_type"].items():
                print(f"    {key:30} distinct sessions={v['distinct_sessions']} distinct chunks per turn={v['distinct_chunks_per_turn']}")
        print("  A " + (a["halted"] if "halted" in a else
              f"median shared top5={a['median_shared_top5']} mean={a['mean_shared_top5']:.2f} baseline={a['random_baseline_shared_top5']:.2f} "
              f"same_top1={a['same_top1_pairs']}/{a['pair_count']} distinct_top1={a['distinct_top1_chunks']} -> {a['verdict']}"))
        print("  B " + (b["halted"] if "halted" in b else f"near-tie rate={b['near_tie_rate']:.2f} gap median={b['gap_distribution']['median']:.4f}"))
        print(f"  C boundary p{c['boundary_page']} nav={c['navigation_pages']} " + (c["halted"] if "halted" in c else
              f"base={c['base_rate']:.3f} top1={c['top1_pick_rate']:.3f} ratio={c['ratio']:.2f} nav_picks={c['top1_navigation_picks']} "
              f"session_rate={c['session_pick_rate']:.3f} session_nav_picks={c['session_navigation_picks']} -> over_retrieved={c['over_retrieved']}"))
        print(f"  D over 256: {dd['over_limit']}/{dd['chunks']}, mean seen share={dd['mean_seen_share']:.3f}")
        print("  E " + (e["halted"] if "halted" in e else f"max displacement={e['max_displacement_rank']} over5={e['turns_over_5']}/{e['turn_count']} displaced={e['turns_displaced']}"))
    for name, r in output.get("literature_routing", {}).items():
        print(f"routing {name[:50]} access={r['access']} tagged={len(r['tagged_chunks'])}")
        for a, v in r["per_archetype"].items():
            b, f = v["before"], v["after"]
            print(f"  {a:34} top1 {b['top1']}->{f['top1']}  plain {b['plain_turns']}->{f['plain_turns']}  "
                  f"seeded {b['seeded_turns']}/{b['seeded_turns_total']}->{f['seeded_turns']}/{f['seeded_turns_total']}")
    print("summary:", json.dumps(output["summary"], indent=1))
    print(f"longest string in results JSON: {longest} characters")


if __name__ == "__main__":
    main()
