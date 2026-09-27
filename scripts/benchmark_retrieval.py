"""Offline comparison; output contains ranks and hashes, never document passages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from academic_defense_simulator.rag.chunking import chunk_pdf, chunk_pages, extract_pages
from academic_defense_simulator.rag.embeddings import EmbeddingModel


def normalize(text):
    return " ".join(text.split())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents-dir", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/retrieval_results.json")
    args = parser.parse_args()
    import torch
    torch.set_num_threads(2)
    model = EmbeddingModel()
    labels = json.loads((ROOT / "evals/retrieval_cases.json").read_text())
    output = {"model": model.model_name, "max_seq_length": model.max_seq_length, "documents": []}
    for document in labels["documents"]:
        path = args.documents_dir / document["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
            raise ValueError(f"Document hash mismatch: {path.name}")
        pages = extract_pages(path)
        for case in document["cases"]:
            assert normalize(case["evidence"]) in normalize(pages[case["page"] - 1].text), case["id"]
        queries = np.array(model.encode([case["query"] for case in document["cases"]]))
        result = {"file": path.name, "sha256": document["sha256"], "variants": {}}
        windows = chunk_pages(pages, model)
        window_vectors = np.array(model.encode([p.text for p in windows]))
        page_texts = [p.text for p in pages if p.text.strip()]
        page_vectors = []
        for page in pages:
            if not page.text.strip():
                continue
            vector = window_vectors[[i for i, w in enumerate(windows) if w.page_number == page.page_number]].mean(axis=0)
            page_vectors.append(vector / np.linalg.norm(vector))
        for variant, texts in [("legacy_chars", chunk_pdf(path)),
                               ("page_tokens", [p.text for p in windows]),
                               ("page_pooled", page_texts)]:
            lengths = [len(model.tokenizer.encode(t, truncation=False)) for t in texts]
            matrix = np.array(page_vectors) if variant == "page_pooled" else (window_vectors if variant == "page_tokens" else np.array(model.encode(texts)))
            scores = queries @ matrix.T
            rows = []
            for case, similarities in zip(document["cases"], scores):
                order = np.argsort(-similarities, kind="stable")
                relevant = {i for i, text in enumerate(texts)
                            if normalize(case["evidence"]) in normalize(text)}
                rank = next((rank for rank, i in enumerate(order, 1) if i in relevant), None)
                rows.append({"id": case["id"], "first_evidence_rank": rank,
                             "hit_at_1": rank == 1, "hit_at_3": rank is not None and rank <= 3})
            result["variants"][variant] = {
                "chunks": len(texts), "max_tokens": max(lengths),
                "truncated_chunks": 0 if variant == "page_pooled" else sum(n > model.max_seq_length for n in lengths),
                "hit_at_1": sum(r["hit_at_1"] for r in rows) / len(rows),
                "hit_at_3": sum(r["hit_at_3"] for r in rows) / len(rows), "cases": rows}
        output["documents"].append(result)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
