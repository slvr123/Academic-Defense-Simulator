"""scripts/e2e_defense.py against the test app and the fake Gemini: the same run
it makes against a real backend, without spending quota."""

from __future__ import annotations

from backend.tests.conftest import make_pdf
from scripts.e2e_defense import auto_answer, run_defense


def test_the_script_runs_a_whole_defense(client_factory, tmp_path):
    pdf = make_pdf(tmp_path / "thesis.pdf", pages=16)
    lines: list[str] = []
    with client_factory() as client:
        record = run_defense(client, pdf, panel=None, answer_for=auto_answer, log=lines.append, poll_s=0.01)
    assert len(record["turns"]) == 4 and record["report"]["report"]["narrative"]
    assert record["quota_before"]["used"] == 0 and record["quota_after"]["used"] == 1
    assert record["memory"] == [] and record["chunk_copy"] is None  # /debug/memory is off here


def test_the_script_records_the_live_chunk_copy(client_factory, tmp_path):
    pdf = make_pdf(tmp_path / "thesis.pdf", pages=16)
    lines: list[str] = []
    with client_factory(debug_memory=True) as client:
        record = run_defense(client, pdf, panel=None, answer_for=auto_answer, log=lines.append, poll_s=0.01)
    copy = record["chunk_copy"]
    assert copy["chunks"] == 8 and 0 < copy["mb"] < 1  # 8 chunks of 384 floats: about 0.1 MB
    assert [m["at"] for m in record["memory"]] == ["before start", "after start", "after the last answer"]
    assert any(line.startswith("  chunk copy for this defense:") for line in lines)
    assert record["turns"][0]["answer"].startswith("As we wrote on page ")
    assert any("report: 4 turns" in line for line in lines)
