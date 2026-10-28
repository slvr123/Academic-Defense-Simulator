"""M8 K5: ADS_TTS_VOICE_MODE. `design` is today's VoiceDesign request, byte for byte;
`clone` sends the asking archetype's reference clip with the text, through the same
/tts route (limits, validation, logging without text). MiMo is never called: httpx.post
is replaced by a recorder in both v1's client and backend.tts_clone."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx
import pytest

import academic_defense_simulator.mimo_provider as v1_mimo
import backend.tts_clone as tts_clone
from academic_defense_simulator.panel import MIMO_VOICE_DESCRIPTIONS
from backend.app import create_app
from backend.settings import Settings
from backend.tests.conftest import make_pdf, make_settings, upload, wait_for
from backend.tests.fake_mimo import WAV


class RecordedPost:
    """Stands in for httpx.post: records each request and answers like MiMo."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[dict] = []
        self.status = status

    def __call__(self, url, *, headers, json, timeout):
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        body = {"choices": [{"message": {"audio": {"data": base64.b64encode(WAV).decode()}}}]}
        return httpx.Response(self.status, json=body, request=httpx.Request("POST", url))


@pytest.fixture
def post(monkeypatch) -> RecordedPost:
    recorder = RecordedPost()
    monkeypatch.setattr(v1_mimo.httpx, "post", recorder)  # the same module object as tts_clone's httpx
    return recorder


def test_the_mode_is_design_by_default():
    assert Settings(_env_file=None).tts_voice_mode == "design"


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        Settings(_env_file=None, tts_voice_mode="preset")


def test_design_mode_is_v1s_synthesize_speech_itself():
    assert tts_clone.synthesize_for("design") is v1_mimo.synthesize_speech


def test_design_mode_sends_todays_request_byte_for_byte(post):
    audio = tts_clone.synthesize_for("design")("Why that board?", "methodology_expert", "k")
    assert audio == WAV
    [request] = post.requests
    expected = {
        "model": "mimo-v2.5-tts-voicedesign",
        "messages": [
            {"role": "user", "content": MIMO_VOICE_DESCRIPTIONS["methodology_expert"]},
            {"role": "assistant", "content": "Why that board?"},
        ],
        "audio": {"format": "wav"},
    }
    assert json.dumps(request["json"]) == json.dumps(expected)
    assert (request["url"], request["timeout"]) == (v1_mimo.API_URL, 12.0)


def test_every_archetype_with_a_voice_has_a_committed_reference_clip():
    refs = tts_clone.load_voice_refs()
    assert set(refs) == set(MIMO_VOICE_DESCRIPTIONS)
    sizes = [path.stat().st_size for path in tts_clone.VOICE_REFS.glob("*.wav")]
    assert len(sizes) == 9 and sum(sizes) < 3 * 1024 * 1024
    for key, uri in refs.items():
        assert base64.b64decode(uri.removeprefix("data:audio/wav;base64,"))[:4] == b"RIFF", key


def test_clone_mode_sends_the_archetypes_reference_with_the_text(post):
    synthesize = tts_clone.synthesize_for("clone")
    assert synthesize("Why that board?", "devils_advocate", "k") == WAV
    [request] = post.requests
    clip = (tts_clone.VOICE_REFS / "devils_advocate.wav").read_bytes()
    assert request["json"] == {
        "model": "mimo-v2.5-tts-voiceclone",
        "messages": [{"role": "user", "content": ""}, {"role": "assistant", "content": "Why that board?"}],
        "audio": {"format": "wav", "voice": "data:audio/wav;base64," + base64.b64encode(clip).decode()},
    }
    assert request["headers"]["api-key"] == "k" and request["url"] == v1_mimo.API_URL


def test_clone_mode_refuses_to_start_without_every_reference(tmp_path: Path):
    (tmp_path / "methodology_expert.wav").write_bytes(WAV)
    with pytest.raises(FileNotFoundError, match="devils_advocate"):
        tts_clone.load_voice_refs(tmp_path)


def test_a_clone_failure_is_a_mimo_error(monkeypatch):
    monkeypatch.setattr(v1_mimo.httpx, "post", RecordedPost(status=500))
    with pytest.raises(v1_mimo.MimoTTSError, match="clone synthesis failed"):
        tts_clone.clone_synthesize({"devils_advocate": "data:audio/wav;base64,"})("x", "devils_advocate", "k")
    with pytest.raises(v1_mimo.MimoTTSError):
        tts_clone.clone_synthesize({})("x", "devils_advocate", "k")  # no reference: a KeyError, wrapped


@pytest.fixture
def clone_app(tmp_path, embedder, quota_store, clock, gemini):
    """The real app with no injected synthesize, so ADS_TTS_VOICE_MODE decides."""

    def build(**overrides):
        settings = make_settings(upload_dir=tmp_path / "uploads", mimo_api_key="k", **overrides)
        return create_app(settings, embedder_factory=lambda _s: embedder, quota_store_factory=lambda _s: quota_store,
                          clock=clock, providers_factory=lambda _s: gemini.providers)

    return build


@pytest.mark.parametrize(("mode", "model"), [("design", "mimo-v2.5-tts-voicedesign"), ("clone", "mimo-v2.5-tts-voiceclone")])
def test_tts_through_the_route_in_each_mode_with_split_parts(clone_app, post, tmp_path, embedder, caplog, mode, model):
    from fastapi.testclient import TestClient

    caplog.set_level(logging.INFO, logger="backend")
    with TestClient(clone_app(tts_voice_mode=mode, tts_split_sentences=True)) as client:
        try:
            session_id = upload(client, make_pdf(tmp_path / "Library System.pdf", pages=16)).json()["session_id"]
            wait_for(client, session_id, lambda s: s.get("status") == "ready")
            start = client.post(f"/sessions/{session_id}/start")
            assert start.status_code == 200, start.text
            question = start.json()["question"]
            response = client.post("/tts", json={"session_id": session_id, "turn": question["turn"], "part": 0})
        finally:
            embedder.release()
    assert response.status_code == 200 and response.content == WAV
    assert int(response.headers["X-TTS-Parts"]) >= 1
    assert [r["json"]["model"] for r in post.requests] == [model]
    assert ("voice" in post.requests[0]["json"]["audio"]) is (mode == "clone")
    assert f"tts voice_mode={mode} split_sentences=True" in caplog.text
    tts_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tts session=")]
    assert tts_lines and question["question"] not in "\n".join(tts_lines)  # no question text in the log
