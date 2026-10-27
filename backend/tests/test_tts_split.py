"""v2.0 M6 H9: the sentence splitter behind part-by-part voices (backend/tts_split.py)."""

from __future__ import annotations

import pytest

from backend.tts_split import MAX_PARTS, MIN_PART_CHARS, split_sentences


def test_splits_at_sentence_ends():
    text = "Your study used thirty respondents. Why did you choose that number? Explain how it supports the claim!"
    assert split_sentences(text) == [
        "Your study used thirty respondents.",
        "Why did you choose that number?",
        "Explain how it supports the claim!",
    ]


def test_joining_the_parts_gives_the_question_back():
    text = "First   sentence here, with spacing.\nSecond sentence on a new line? Third one."
    assert " ".join(split_sentences(text)) == " ".join(text.split())


@pytest.mark.parametrize(
    "text",
    [
        "The mean was 4.70 on the usability scale, which you call excellent.",
        "You cite standards, e.g. ISO 25010, without saying which criteria you dropped.",
        "As Cruz et al. 2020 argue, barcode systems drift over time.",
        "Section 3.2 says the sample was purposive, i.e. Hand-picked by the librarian.",
        "Dr. Reyes reviewed the instrument before the pilot test began.",
        "See Fig. 4 and the table that follows it for the breakdown.",
    ],
)
def test_abbreviations_and_decimals_dont_split(text):
    assert split_sentences(text) == [text]


def test_closing_quotes_stay_with_their_sentence():
    text = 'You call the system "reliable." What evidence supports that word?'
    assert split_sentences(text) == ['You call the system "reliable."', "What evidence supports that word?"]


def test_a_lowercase_continuation_is_not_a_new_sentence():
    text = "The score was high. and yet the librarian kept paper records for the whole semester."
    assert split_sentences(text) == [text]


def test_short_pieces_join_a_neighbour():
    assert split_sentences("Fine. Now explain why your sample excludes the teachers.") == [
        "Fine. Now explain why your sample excludes the teachers."
    ]
    assert split_sentences("Explain why your sample excludes the teachers. Why?") == [
        "Explain why your sample excludes the teachers. Why?"
    ]
    assert all(len(part) >= MIN_PART_CHARS for part in split_sentences("A long enough opening sentence. Ok. Another long sentence follows."))


def test_never_more_than_max_parts():
    text = " ".join(f"This is sentence number {n} of the question." for n in range(12))
    parts = split_sentences(text)
    assert len(parts) == MAX_PARTS
    assert " ".join(parts) == text


def test_blank_and_unpunctuated_text():
    assert split_sentences("   \n ") == []
    assert split_sentences("no punctuation at all in this question") == ["no punctuation at all in this question"]


def test_deterministic():
    text = "One sentence that is long enough. Another sentence that is long enough? A third."
    assert split_sentences(text) == split_sentences(text)
