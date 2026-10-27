"""A question split into sentences for part-by-part voices (v2.0 M6 H9).

Pure and deterministic: the same question always splits the same way, so a client
asking for part n gets the same sentence however many times it asks. Splits at
. ! or ? followed by a space and a capital, digit or opening quote, except after a
known abbreviation, so "e.g. ISO 25010" and "4.70" stay whole. Very short pieces
join their neighbour (a two-word sentence isn't worth its own synthesis call), and
a question never has more than MAX_PARTS parts.
"""

from __future__ import annotations

import re

MAX_PARTS = 8
MIN_PART_CHARS = 20

# Where a sentence can end: terminal punctuation, any closing quotes or brackets,
# then whitespace and something that starts a sentence.
_SENTENCE_END = re.compile(r"[.!?][\"')\]’”]*(?=\s+[\"'(\[‘“]?[A-Z0-9])")
_ABBREVIATIONS = (
    "e.g.", "i.e.", "et al.", "etc.", "vs.", "cf.", "approx.", "dr.", "mr.", "mrs.", "ms.", "prof.",
    "fig.", "figs.", "no.", "nos.", "vol.", "pp.", "p.", "eq.", "sec.", "ch.", "st.",
)


def _ends_with_abbreviation(piece: str) -> bool:
    words = piece.lower().split()
    if not words:
        return False
    last_two = " ".join(words[-2:])
    return words[-1] in _ABBREVIATIONS or last_two in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", words[-1]) is not None


def split_sentences(text: str) -> list[str]:
    """The question's parts, in order; [] for blank text. Joining them with spaces
    gives the question back with its whitespace collapsed."""
    text = " ".join(text.split())
    if not text:
        return []
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        piece = text[start : match.end()]
        if _ends_with_abbreviation(piece):
            continue
        sentences.append(piece.strip())
        start = match.end()
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return _cap(_merge_short(sentences))


def _merge_short(sentences: list[str]) -> list[str]:
    merged: list[str] = []
    for sentence in sentences:
        if merged and (len(sentence) < MIN_PART_CHARS or len(merged[-1]) < MIN_PART_CHARS):
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    return merged


def _cap(sentences: list[str]) -> list[str]:
    if len(sentences) <= MAX_PARTS:
        return sentences
    return sentences[: MAX_PARTS - 1] + [" ".join(sentences[MAX_PARTS - 1 :])]
