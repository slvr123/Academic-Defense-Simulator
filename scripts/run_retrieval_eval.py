"""v2.0 M4 retrieval eval: checks A-E on the production retrieval stack, offline.

Usage (from the repo root, in backend/.venv, which has the ONNX runtime):

    python scripts/run_retrieval_eval.py               # production ingest (M5: navigation filter)
    python scripts/run_retrieval_eval.py --chunker v1  # v1's chunker, as M4 measured it

Chunks come from the backend's own `parse_pdf`, embeddings from `OnnxEmbedder`
(backend/model), and every simulated session is also run through v1's
`retrieve()` on `engine_chunks`, the exact objects a live defense uses. No model
API is called. Check E's validation against the M3 live run comes first and
stops the run on a mismatch. That run predates M5, so only `--chunker v1` can
reproduce it; on the production chunker the replay is still checked against
`retrieve()` turn by turn.

Writes evals/v2.0-m5-retrieval-results.json (production) or the M4 file
evals/v2.0-m4-retrieval-results.json (`--chunker v1`): ranks, scores, page numbers, chunk
indices and hashes, never passage text. See docs/v2.0-m4-retrieval-eval-decisions.md.
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
from backend.page_filter import front_matter_boundary, removable_pages  # noqa: E402
from backend.pdf_ingest import IngestFailure, parse_pdf  # noqa: E402
from backend.schemas import MAX_DOMAIN_PANELISTS  # noqa: E402
from backend.sessions import ChunkMeta  # noqa: E402
from evals import retrieval_checks as rc  # noqa: E402

MODEL_DIR = ROOT / "backend" / "model"
OUTPUTS = {
    "production": ROOT / "evals" / "v2.0-m5-retrieval-results.json",
    "v1": ROOT / "evals" / "v2.0-m4-retrieval-results.json",
}
SEPTEMBER_CASES = "feat/retrieval-sources:evals/retrieval_cases.json"
# Not in F4's list (repo root or the September cases), so reported but kept out of every verdict.
SUPPLEMENTARY = [ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf"]
SESSION_TURNS = 6  # F5 Check E; the engine caps a session at one new topic per domain panelist

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


def parse_pdf_v1(path: Path) -> list[PageChunk]:
    """`parse_pdf` as it was before M5: v1's chunker, with the same IngestFailure mapping."""
    try:
        chunks = chunk_pdf_with_pages(path)
    except InsufficientTextError as exc:
        raise IngestFailure("scanned", code="scanned_pdf") from exc
    except DocumentIngestionError as exc:
        raise IngestFailure(str(exc)) from exc
    if not chunks:
        raise IngestFailure("scanned", code="scanned_pdf")
    return chunks


PARSERS = {"production": parse_pdf, "v1": parse_pdf_v1}


def load_document(path: Path, in_verdict: bool, embedder, counter, parse) -> Document:
    """Ingest as IngestQueue._ingest does it: `parse` (production: backend.pdf_ingest.parse_pdf),
    then OnnxEmbedder.encode_one per chunk."""
    page_chunks = parse(path)
    chunks = [ChunkMeta(chunk_id=i, text=c.text, page_start=c.page_start, page_end=c.page_end) for i, c in enumerate(page_chunks)]
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
    )


def recommended_panel(composition_key: str) -> list[str]:
    """The frontend's "Use recommended panel": the roster's first max_domain_panelists."""
    return PANEL_COMPOSITION[composition_key][:MAX_DOMAIN_PANELISTS]


def production_session(doc: Document, panel: list[str], embedder) -> list[tuple[int, float]]:
    """The same turns through v1's own retrieve(), on the engine's chunk objects:
    (chunk index, score as SourceTracker computes it) per new-topic turn."""
    chunks = engine_chunks(doc.matrix, doc.chunks)
    encoder = RecordingEncoder(embedder)
    used: set[int] = set()
    turns = []
    for archetype in panel[:SESSION_TURNS]:
        query = ARCHETYPE_CONFIG[archetype]["archetype_focus"]
        index, _ = retrieve(query, chunks, top_k=1, embedding_model=encoder, exclude_indices=frozenset(used))[0]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="v2.0 retrieval eval, checks A-E")
    parser.add_argument("--chunker", choices=sorted(PARSERS), default="production")
    args = parser.parse_args()
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
            doc = load_document(path, in_verdict, embedder, counter, parse)
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
    scores = {d.sha256: {a: d.matrix @ query_vectors[a] for a in RETRIEVING_ARCHETYPES} for d in docs}
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
            # Only on the production chunker, so the v1 run stays byte-identical to M4's file.
            **({"page_filter": removal_report(doc)} if args.chunker == "production" else {}),
        })

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
        "milestone": "v2.0 M4" if args.chunker == "v1" else "v2.0 M5",
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
    longest = longest_string(output)
    if longest > 200:
        raise SystemExit(f"HALT: a string of {longest} characters would go into the results JSON")
    OUTPUTS[args.chunker].write_text(json.dumps(output, indent=1) + "\n", encoding="utf-8")
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
        print("  A " + (a["halted"] if "halted" in a else
              f"median shared top5={a['median_shared_top5']} mean={a['mean_shared_top5']:.2f} baseline={a['random_baseline_shared_top5']:.2f} "
              f"same_top1={a['same_top1_pairs']}/{a['pair_count']} distinct_top1={a['distinct_top1_chunks']} -> {a['verdict']}"))
        print("  B " + (b["halted"] if "halted" in b else f"near-tie rate={b['near_tie_rate']:.2f} gap median={b['gap_distribution']['median']:.4f}"))
        print(f"  C boundary p{c['boundary_page']} nav={c['navigation_pages']} " + (c["halted"] if "halted" in c else
              f"base={c['base_rate']:.3f} top1={c['top1_pick_rate']:.3f} ratio={c['ratio']:.2f} nav_picks={c['top1_navigation_picks']} "
              f"session_rate={c['session_pick_rate']:.3f} session_nav_picks={c['session_navigation_picks']} -> over_retrieved={c['over_retrieved']}"))
        print(f"  D over 256: {dd['over_limit']}/{dd['chunks']}, mean seen share={dd['mean_seen_share']:.3f}")
        print("  E " + (e["halted"] if "halted" in e else f"max displacement={e['max_displacement_rank']} over5={e['turns_over_5']}/{e['turn_count']} displaced={e['turns_displaced']}"))
    print("summary:", json.dumps(output["summary"], indent=1))
    print(f"longest string in results JSON: {longest} characters")


if __name__ == "__main__":
    main()
