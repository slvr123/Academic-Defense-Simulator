"""Endpoint behaviour: validation, queue and session caps, worker resilience, TTL."""

from __future__ import annotations

import json
import time
from pathlib import Path

from academic_defense_simulator.rag.chunking import chunk_pdf_with_pages
from backend.settings import Settings
from backend.tests.conftest import CONTENTS_MARKER, make_navigation_pdf, make_pdf, upload, wait_for

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _ready(body: dict) -> bool:
    return body.get("status") == "ready"


def _embedding(body: dict) -> bool:
    return body.get("status") == "embedding"


# --- happy path ---


def test_health_reports_model_loaded(client_factory):
    with client_factory(mimo_api_key=None) as client:
        assert client.get("/health").json() == {"status": "ok", "model_loaded": True, "tts_available": False}


def test_pdf_goes_from_queued_to_ready_with_page_ranges(client_factory, pdf):
    with client_factory() as client:
        response = upload(client, pdf)
        assert response.status_code == 202
        created = response.json()
        assert created["status"] == "queued" and created["queue_position"] == 1

        state = wait_for(client, created["session_id"], _ready)
        expected = chunk_pdf_with_pages(pdf)
        assert state == {"status": "ready", "progress": 1.0, "chunk_count": len(expected), "defense": "not_started"}

        session = client.app.state.store.get(created["session_id"])
        assert session.matrix.shape == (len(expected), 384)
        assert [(c.page_start, c.page_end) for c in session.chunks] == [
            (c.page_start, c.page_end) for c in expected
        ]
        assert session.panel.starting_difficulty == 2  # panel omitted -> defaults


def test_an_uploaded_contents_page_never_reaches_a_chunk(client_factory, tmp_path):
    # M5: title, contents, abstract, Chapter 1, results. Only the contents page goes.
    path = make_navigation_pdf(tmp_path / "thesis.pdf", ["title", "contents", "abstract", "chapter_one", "results"])
    with client_factory() as client:
        session_id = upload(client, path).json()["session_id"]
        assert wait_for(client, session_id, _ready)["chunk_count"] >= 1
        chunks = client.app.state.store.get(session_id).chunks
    assert not any(CONTENTS_MARKER in c.text for c in chunks)
    assert all("Table of Contents" not in c.text for c in chunks)
    assert any("Abstract" in c.text for c in chunks) and any("Total respondents 30" in c.text for c in chunks)
    # Page numbers are the PDF's own: the text starts on the title page and ends on the results page.
    assert (chunks[0].page_start, chunks[-1].page_end) == (0, 4)


def test_progress_increases_one_chunk_at_a_time(client_factory, tmp_path, embedder):
    path = make_pdf(tmp_path / "long.pdf", pages=8)
    total = len(chunk_pdf_with_pages(path))
    assert total >= 3
    embedder.stepwise()
    with client_factory() as client:
        session_id = upload(client, path).json()["session_id"]
        seen = [wait_for(client, session_id, _embedding)["progress"]]
        for done in range(1, total + 1):
            embedder.step()
            body = wait_for(client, session_id, lambda b, d=done: b.get("progress") == d / total)
            seen.append(body["progress"])
        assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0
        assert wait_for(client, session_id, _ready)["chunk_count"] == total


# --- upload validation ---


def test_not_a_pdf_is_422(client_factory, tmp_path):
    fake = tmp_path / "notes.pdf"
    fake.write_text("just some notes, not a PDF")
    with client_factory() as client:
        response = upload(client, fake)
    assert response.status_code == 422
    assert "isn't a PDF" in response.json()["detail"]


def test_damaged_pdf_is_422(client_factory, tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    with client_factory() as client:
        response = upload(client, broken)
    assert response.status_code == 422
    assert "couldn't be opened" in response.json()["detail"]


def test_missing_file_field_is_422(client_factory):
    with client_factory() as client:
        response = client.post("/sessions", data={"panel": "{}"})
    assert response.status_code == 422


def test_oversize_rejected_by_content_length_before_the_body_is_read(client_factory, tmp_path):
    big = make_pdf(tmp_path / "big.pdf", pages=250)
    assert big.stat().st_size > 64 * 1024 + 1024
    with client_factory(max_upload_mb=1 / 1024) as client:  # 1 KB limit
        response = upload(client, big)
    assert response.status_code == 413
    assert "larger than" in response.json()["detail"]


def test_oversize_rejected_while_spooling(client_factory, pdf):
    # Under the content-length pre-check's multipart slack, over the real limit.
    assert 1024 < pdf.stat().st_size < 64 * 1024
    with client_factory(max_upload_mb=1 / 1024) as client:
        response = upload(client, pdf)
        assert response.status_code == 413
        assert list(client.app.state.settings.upload_dir.iterdir()) == []


def test_too_many_pages_is_413(client_factory, tmp_path):
    long = make_pdf(tmp_path / "long.pdf", pages=4)
    with client_factory(max_pages=3) as client:
        response = upload(client, long)
        assert response.status_code == 413
        assert response.json()["detail"] == "That PDF has 4 pages; the limit is 3."
        assert list(client.app.state.settings.upload_dir.iterdir()) == []


def test_invalid_panel_is_422(client_factory, pdf):
    with client_factory() as client:
        for panel in (
            '{"archetype_keys": ["not_a_panelist"]}',
            '{"starting_difficulty": 9}',
            '{"archetype_keys": ["methodology_expert", "methodology_expert"]}',
            '{"unexpected": true}',
            "not json",
            '{"archetype_keys": ["devils_advocate"]}',  # always on the panel, never chosen
            '{"defense_type": "other"}',  # needs other_subtype
            '{"defense_type": "capstone", "archetype_keys": ["not_a_panelist"]}',
        ):
            assert upload(client, pdf, panel=panel).status_code == 422, panel


def test_valid_panel_is_stored(client_factory, pdf):
    panel = {
        "defense_type": "capstone", "domain": "Library science", "topic": "A library system",
        "archetype_keys": ["methodology_expert", "industry_practice_reviewer"], "starting_difficulty": 4,
    }
    with client_factory() as client:
        session_id = upload(client, pdf, panel=json.dumps(panel)).json()["session_id"]
        stored = client.app.state.store.get(session_id).panel
    assert stored.model_dump(mode="json") == panel | {"other_subtype": None}


# --- failures and the worker ---


def test_scanned_pdf_fails_with_a_looks_scanned_error(client_factory, tmp_path):
    scanned = make_pdf(tmp_path / "scanned.pdf", pages=2, text=False)
    with client_factory() as client:
        session_id = upload(client, scanned).json()["session_id"]
        state = wait_for(client, session_id, lambda b: b.get("status") == "failed")
    assert "looks scanned" in state["error"]
    assert state["error_code"] == "scanned_pdf"
    assert "chunk_count" not in state


def test_worker_keeps_running_after_a_failed_job(client_factory, tmp_path, pdf):
    scanned = make_pdf(tmp_path / "scanned.pdf", pages=2, text=False)
    with client_factory() as client:
        bad = upload(client, scanned).json()["session_id"]
        good = upload(client, pdf).json()["session_id"]
        assert wait_for(client, bad, lambda b: b.get("status") == "failed")
        assert wait_for(client, good, _ready)


def test_worker_survives_an_embedder_crash(client_factory, pdf, embedder, monkeypatch):
    calls = {"n": 0}
    original = embedder.encode_one

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(text)

    monkeypatch.setattr(embedder, "encode_one", flaky)
    with client_factory() as client:
        first = upload(client, pdf).json()["session_id"]
        state = wait_for(client, first, lambda b: b.get("status") == "failed")
        assert state["error"].startswith("Something went wrong")
        assert "boom" not in state["error"]  # internals stay in the server log
        second = upload(client, pdf).json()["session_id"]
        assert wait_for(client, second, _ready)


# --- queue and session caps ---


def test_queue_positions_and_503_on_the_fourth_waiting_job(client_factory, pdf, embedder):
    embedder.hold()
    with client_factory() as client:
        running = upload(client, pdf).json()["session_id"]
        wait_for(client, running, _embedding)

        waiting = []
        for expected_position in (1, 2, 3):
            response = upload(client, pdf)
            assert response.status_code == 202
            assert response.json()["queue_position"] == expected_position
            waiting.append(response.json()["session_id"])

        fourth = upload(client, pdf)
        assert fourth.status_code == 503
        assert "busy" in fourth.json()["detail"]

        assert "queue_position" not in client.get(f"/sessions/{running}").json()
        assert [client.get(f"/sessions/{s}").json()["queue_position"] for s in waiting] == [1, 2, 3]

        # Deleting a waiting job frees its place at once and moves the others up.
        assert client.delete(f"/sessions/{waiting[0]}").status_code == 204
        assert [client.get(f"/sessions/{s}").json()["queue_position"] for s in waiting[1:]] == [1, 2]
        assert upload(client, pdf).status_code == 202

        embedder.release()
        for session_id in [running, *waiting[1:]]:
            wait_for(client, session_id, _ready)
        assert list(client.app.state.settings.upload_dir.iterdir()) == []


def test_live_session_cap_returns_503(client_factory, pdf):
    with client_factory(max_live_sessions=2) as client:
        first = upload(client, pdf).json()["session_id"]
        upload(client, pdf)
        third = upload(client, pdf)
        assert third.status_code == 503
        assert "busy" in third.json()["detail"]

        wait_for(client, first, _ready)
        assert client.delete(f"/sessions/{first}").status_code == 204
        assert upload(client, pdf).status_code == 202


# --- lookup, delete, TTL ---


def test_unknown_session_is_404(client_factory):
    with client_factory() as client:
        assert client.get("/sessions/00000000-0000-4000-8000-000000000000").status_code == 404
        assert client.delete("/sessions/nope").status_code == 404


def test_delete_frees_a_ready_session(client_factory, pdf):
    with client_factory() as client:
        session_id = upload(client, pdf).json()["session_id"]
        wait_for(client, session_id, _ready)
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        assert client.get(f"/sessions/{session_id}").status_code == 404
        assert len(client.app.state.store) == 0


def test_delete_during_embedding_drops_the_job(client_factory, tmp_path, embedder):
    path = make_pdf(tmp_path / "long.pdf", pages=8)
    embedder.stepwise()
    with client_factory() as client:
        session_id = upload(client, path).json()["session_id"]
        wait_for(client, session_id, _embedding)
        embedder.step()
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        embedder.release()
        follow_up = upload(client, path).json()["session_id"]
        wait_for(client, follow_up, _ready)  # the worker moved on
        assert client.get(f"/sessions/{session_id}").status_code == 404


def test_idle_ready_session_is_evicted_by_the_sweep(client_factory, pdf):
    with client_factory(session_ttl_seconds=1.0, sweep_interval_seconds=0.05) as client:
        session_id = upload(client, pdf).json()["session_id"]
        wait_for(client, session_id, _ready)
        # Polled, not a fixed sleep: the job's gc.collect() calls hold the GIL for
        # a while (about 0.2 s each with torch loaded by the parity test).
        deadline = time.monotonic() + 15  # M6 H7: room for a slow machine; the sweep runs every 0.05 s
        while len(client.app.state.store) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(client.app.state.store) == 0  # the sweep, not a lookup, removed it
        assert client.get(f"/sessions/{session_id}").status_code == 404


def test_startup_clears_leftover_uploads(client_factory, tmp_path):
    leftover_dir = tmp_path / "uploads"
    leftover_dir.mkdir()
    (leftover_dir / "stale.pdf").write_bytes(b"%PDF-old")
    with client_factory():
        assert list(leftover_dir.iterdir()) == []


# --- dependencies ---


# --- /debug/memory ---


def test_debug_memory_is_404_by_default(client_factory, monkeypatch):
    monkeypatch.delenv("ADS_DEBUG_MEMORY", raising=False)
    with client_factory() as client:
        assert client.get("/debug/memory").status_code == 404


def test_debug_memory_reports_the_process_and_the_last_job(client_factory, pdf):
    with client_factory(debug_memory=True, max_pages=250) as client:
        first = client.get("/debug/memory").json()
        assert first["last_job"] is None and first["max_pages"] == 250
        assert first["pid"] > 0 and first["process_started_at"] > 0 and first["rss_mb"] > 0

        session_id = upload(client, pdf).json()["session_id"]
        state = wait_for(client, session_id, _ready)
        after = client.get("/debug/memory").json()
        assert (after["pid"], after["process_started_at"]) == (first["pid"], first["process_started_at"])
        job = after["last_job"]
        assert job["session_id"] == session_id and job["outcome"] == "ready"
        assert job["pages"] == 3 and job["chunks"] == state["chunk_count"]
        assert job["parse_s"] >= 0 and job["embed_s"] >= 0 and job["total_s"] >= job["embed_s"]
        assert job["job_peak_rss_mb"] > 0
        assert [p["phase"] for p in job["phases"]] == ["spooled", "parsed", "released", "embedded", "job_end"]
        highs = [p["hwm_mb"] for p in job["phases"]]
        if None not in highs:  # VmHWM / peak working set only rises
            assert highs == sorted(highs)
        assert all(p["rss_mb"] > 0 for p in job["phases"])


def test_failed_job_still_reports_the_phases_it_reached(client_factory, tmp_path):
    scanned = make_pdf(tmp_path / "scanned.pdf", text=False)
    with client_factory(debug_memory=True) as client:
        session_id = upload(client, scanned).json()["session_id"]
        wait_for(client, session_id, lambda b: b.get("status") == "failed")
        job = client.get("/debug/memory").json()["last_job"]
        assert job["outcome"] == "failed"
        assert [p["phase"] for p in job["phases"]] == ["spooled", "job_end"]


def test_production_requirements_exclude_heavy_ml_packages():
    lines = (BACKEND_DIR / "requirements.txt").read_text().splitlines()
    names = {line.split("==")[0].strip().lower() for line in lines if line.strip() and not line.startswith("#")}
    assert not names & {"torch", "sentence-transformers", "chromadb", "transformers"}


def test_production_defaults_need_no_env_vars(monkeypatch):
    for name in (
        "ADS_MAX_UPLOAD_MB", "ADS_MAX_PAGES", "ADS_DEBUG_MEMORY",
        "ADS_GEMINI_MODEL", "ADS_GEMINI_SUGGESTIONS_MODEL", "ADS_GEMINI_LIMITS", "ADS_QUOTA_MARGIN",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert (settings.max_upload_mb, settings.max_pages) == (30, 250)  # M1 measurement, approved 2026-10-19
    assert settings.debug_memory is False
    # The v1 model split and AI Studio's free-tier numbers, approved 2026-10-20.
    assert (settings.gemini_model, settings.gemini_suggestions_model) == ("gemini-3.1-flash-lite", "gemini-2.5-flash")
    assert {m: (l.rpd, l.rpm) for m, l in settings.gemini_limits.items()} == {
        "gemini-3.1-flash-lite": (500, 15), "gemini-2.5-flash": (20, 5),
    }
    assert settings.quota_margin == 1
