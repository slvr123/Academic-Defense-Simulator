"""Grounding-check tests (Task 3, item 5). Covers is_grounded / grounding_ratio."""

from __future__ import annotations

from academic_defense_simulator.grounding import grounding_ratio, is_grounded

# A realistic chunk excerpt in the methodology-document register used throughout evals.
_CHUNK = (
    "The study surveyed twenty respondents selected through purposive sampling, "
    "split evenly between IT and non-IT backgrounds, to capture usability perception "
    "differences across the five evaluation criteria."
)


def test_exact_substring_passes_with_ratio_one():
    ref = "purposive sampling"
    assert is_grounded(ref, _CHUNK) is True
    assert grounding_ratio(ref, _CHUNK) == 1.0


def test_case_and_whitespace_normalization_still_exact():
    # Mixed case + a newline + doubled spaces normalize to an exact substring match.
    ref = "Purposive   Sampling"
    assert is_grounded(ref, _CHUNK) is True
    assert grounding_ratio(ref, _CHUNK) == 1.0


def test_leading_trailing_whitespace_stripped():
    ref = "\n  twenty respondents  \n"
    assert is_grounded(ref, _CHUNK) is True


def test_fuzzy_match_just_above_threshold_passes():
    # One dropped character — not an exact substring, but a near-verbatim citation the
    # fuzzy path is meant to tolerate. Ratio lands between the threshold and 1.0.
    ref = "twenty respondents selected through purposive samplng"  # 'samplng' typo
    ratio = grounding_ratio(ref, _CHUNK)
    assert 0.85 <= ratio < 1.0
    assert is_grounded(ref, _CHUNK) is True


def test_unrelated_reference_fails_below_threshold():
    ref = "quantum entanglement across photonic waveguide arrays"
    assert grounding_ratio(ref, _CHUNK) < 0.85
    assert is_grounded(ref, _CHUNK) is False


def test_threshold_parameter_is_honored():
    # A partial-but-related reference: passes a lenient threshold, fails a strict one.
    ref = "twenty respondents chosen by convenience sampling instead"
    ratio = grounding_ratio(ref, _CHUNK)
    assert is_grounded(ref, _CHUNK, threshold=ratio - 0.01) is True
    assert is_grounded(ref, _CHUNK, threshold=ratio + 0.01) is False


def test_empty_reference_is_never_grounded():
    # Empty string is trivially a substring of everything — must not pass as grounded.
    assert is_grounded("", _CHUNK) is False
    assert is_grounded("   \n  ", _CHUNK) is False


def test_reference_longer_than_chunk_does_not_crash():
    long_ref = _CHUNK + " and several additional clauses that extend well beyond it"
    # Defined, in range, and not a false exact match.
    ratio = grounding_ratio(long_ref, "short chunk")
    assert 0.0 <= ratio < 0.85
    assert is_grounded(long_ref, "short chunk") is False
