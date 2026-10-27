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


def test_the_script_retries_quota_while_the_store_wakes(client_factory, quota_store, tmp_path, monkeypatch):
    import scripts.e2e_defense as script
    from backend.quota import QuotaUnavailable

    monkeypatch.setattr(script.time, "sleep", lambda _s: None)
    real_used, failures = quota_store.used, iter([True, False])

    def waking(day):
        if next(failures, False):
            raise QuotaUnavailable("server closed the connection unexpectedly")
        return real_used(day)

    quota_store.used = waking
    lines: list[str] = []
    with client_factory() as client:
        record = run_defense(client, make_pdf(tmp_path / "t.pdf", pages=16), panel=None,
                             answer_for=auto_answer, log=lines.append, poll_s=0.01)
    assert record["quota_before"]["used"] == 0
    assert any("waking up" in line and "waiting 3 s" in line for line in lines)


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


def test_the_script_voices_each_question_in_parts_within_its_budget(client_factory, mimo, tmp_path):
    pdf = make_pdf(tmp_path / "thesis.pdf", pages=16)
    lines: list[str] = []
    with client_factory(mimo_api_key="mimo-not-a-real-key", tts_split_sentences=True) as client:
        record = run_defense(client, pdf, panel=None, answer_for=auto_answer, log=lines.append, poll_s=0.01, voice_calls=3)
    voiced = record["voice"]
    assert [v["turn"] for v in voiced] == [1, 2, 3, 4]
    made = [c for v in voiced for c in v["calls"] if "status" in c]
    assert len(made) == 3 == len(mimo.calls)  # the budget, then no more
    assert all(c["status"] == 200 and c["bytes"] > 0 for c in made)
    assert voiced[0]["time_to_first_audio_s"] is not None
    assert voiced[-1]["calls"] == [{"part": 0, "skipped": "budget"}]
    assert any(line.startswith("  voice: ") for line in lines)


def test_without_voice_calls_nothing_is_voiced(client_factory, mimo, tmp_path):
    with client_factory(mimo_api_key="mimo-not-a-real-key") as client:
        record = run_defense(client, make_pdf(tmp_path / "t.pdf", pages=16), panel=None,
                             answer_for=auto_answer, log=lambda _line: None, poll_s=0.01)
    assert record["voice"] == [] and mimo.calls == []
