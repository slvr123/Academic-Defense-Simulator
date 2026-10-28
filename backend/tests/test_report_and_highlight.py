"""M8 K6 and K8 in the backend: the source highlight (v1's grounding check decides,
the span maps back onto the passage as sent, in JavaScript string offsets), and
the report's overall_raw (the mean of the three dimension averages), end to end."""

from __future__ import annotations

import pytest

from academic_defense_simulator.models.report import DefenseReport
from backend.defense import overall_raw
from backend.highlight import highlight_span
from backend.schemas import Highlight
from backend.tests.conftest import make_pdf, upload, wait_for


def _marked(passage: str, span: Highlight | None) -> str | None:
    """The highlighted text, indexing as JavaScript would (UTF-16 code units)."""
    if span is None:
        return None
    units = passage.encode("utf-16-le")
    return units[span.start * 2 : span.end * 2].decode("utf-16-le")


def test_an_exact_reference_is_highlighted_where_it_is():
    passage = "Leaves were collected in May. Each leaf was photographed flat on white card. Then sorted."
    assert _marked(passage, highlight_span("Each leaf was photographed flat on white card.", passage)) == (
        "Each leaf was photographed flat on white card."
    )


def test_case_and_whitespace_differences_still_map_onto_the_original_text():
    passage = "Intro.\nEach  leaf was\n  photographed FLAT on white card. End."
    span = highlight_span("each leaf was photographed flat on white card", passage)
    assert _marked(passage, span) == "Each  leaf was\n  photographed FLAT on white card"


def test_a_fuzzy_match_above_the_threshold_highlights_the_closest_window():
    # v1's windows step a quarter of the reference, so the match starts on one here (offset 0).
    passage = "The kiosk logs every borrower by QR code at the gate. Closing words follow it."
    reference = "The kiosk logs evry borrower by QR code at the gate."
    assert reference.lower() not in passage.lower()
    assert _marked(passage, highlight_span(reference, passage)) == "The kiosk logs every borrower by QR code at the gate"


def test_an_ungrounded_reference_gets_no_highlight():
    passage = "The kiosk logs every borrower by QR code at the gate."
    assert highlight_span("The survey had thirty respondents from two schools.", passage) is None
    assert highlight_span("", passage) is None


def test_offsets_count_utf16_units_past_a_character_outside_the_bmp():
    passage = "Let \U0001d465 be the load. The server answered within two seconds on average."
    span = highlight_span("The server answered within two seconds", passage)
    assert span is not None
    assert span.start == passage.index("The server") + 1  # the math italic x is two UTF-16 units
    assert _marked(passage, span) == "The server answered within two seconds"


def _report(clarity: float, depth: float, grounding: float) -> DefenseReport:
    return DefenseReport.model_construct(overall_avg_clarity=clarity, overall_avg_depth=depth, overall_avg_grounding=grounding)


@pytest.mark.parametrize(
    ("averages", "expected"),
    [((3.0, 3.0, 3.0), 3.0), ((4.0, 3.0, 2.0), 3.0), ((5.0, 4.5, 4.0), 4.5), ((1.0, 1.0, 1.0), 1.0), ((3.5, 2.25, 4.0), 3.25)],
)
def test_overall_raw_is_the_mean_of_the_three_dimension_averages(averages, expected):
    assert overall_raw(_report(*averages)) == pytest.approx(expected)


def test_the_report_and_every_question_carry_the_new_fields(client_factory, tmp_path):
    with client_factory() as client:
        session_id = upload(client, make_pdf(tmp_path / "Library System.pdf", pages=16)).json()["session_id"]
        wait_for(client, session_id, lambda s: s.get("status") == "ready")
        question = client.post(f"/sessions/{session_id}/start").json()["question"]
        assert question["grounding_reference"]
        assert "highlight" in question["sources"][0]
        while True:
            body = client.post(f"/sessions/{session_id}/answers", json={"text": "We used thirty respondents."}).json()
            if body["done"]:
                break
        report = client.get(f"/sessions/{session_id}/report").json()
    averages = report["report"]
    assert report["overall_raw"] == pytest.approx(
        (averages["overall_avg_clarity"] + averages["overall_avg_depth"] + averages["overall_avg_grounding"]) / 3
    )
    for turn in report["transcript"]:
        source = turn["sources"][0]
        assert turn["grounding_reference"]
        span = source["highlight"]
        if span is not None:
            assert 0 <= span["start"] < span["end"] <= len(source["text"].encode("utf-16-le")) // 2
