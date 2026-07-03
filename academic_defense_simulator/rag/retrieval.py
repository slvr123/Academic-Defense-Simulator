"""Chunk retrieval helpers."""

from __future__ import annotations

from typing import Iterable


def retrieve(query: str, chunks: Iterable[str], top_k: int = 5) -> list[str]:
    scored = []
    query_terms = set(query.lower().split())
    for chunk in chunks:
        chunk_terms = set(chunk.lower().split())
        score = len(query_terms & chunk_terms)
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for score, chunk in scored[:top_k] if score > 0]
