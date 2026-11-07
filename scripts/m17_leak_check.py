"""M17 leak test: a full own-key defense with a sentinel key, then a grep for it.

    python scripts/m17_leak_check.py evidence/v2.0-m17-leak

It runs the real FastAPI app under a real uvicorn server (access log on), with every
log record of every logger written at DEBUG, stricter than production's INFO. Two
passes, each on its own server:

1. **fake**: the fake Gemini provider. Checks the key, uploads with it, runs the
   defense to its report, and on the way forces own_key_limited and own_key_rejected
   (each then resent), a refused key on /keys/check and on the upload, and reads
   /current, the session, the report, /quota and /status.
2. **real-client**: v1's real GeminiProvider and Google's real client, given the
   sentinel. It isn't a real key, so Google refuses it: /start comes back
   own_key_rejected, and Google's own error text goes through v1's logging and the
   route's. One /keys/check with v1's real models.list probe as well. No quota is
   spent, because Google refuses every call.

It writes, to the output directory:
- `log.txt`: every log line from both passes;
- `responses.json`: every response's status, headers and body;
- `dumps.txt`: the settings dump, and repr() of the session and the defense;
- `requests-sent.txt`: the control, every request header this script sent, which
  must contain the sentinel, or the grep proves nothing.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from academic_defense_simulator.llm.provider import LLMProviderError, LLMQuotaExhaustedError  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.quota import InMemoryQuotaStore  # noqa: E402
from backend.settings import Settings  # noqa: E402
from backend.tests.conftest import FakeEmbedder, FakeKeyChecker, make_pdf  # noqa: E402
from backend.tests.fake_gemini import FakeGemini  # noqa: E402

SENTINEL = "AIzaSyM17-LEAK-SENTINEL-c4f1a9e2"
WRONG_KEY = "AIzaSyM17-wrong-key-0000"
SERVER_KEY = "AIzaSyM17-server-key-not-real"


class Recorder:
    def __init__(self) -> None:
        self.responses: list[dict] = []
        self.sent: list[str] = []

    def hook_request(self, request: httpx.Request) -> None:
        self.sent.append(f"{request.method} {request.url.path} {dict(request.headers)}")

    def hook_response(self, response: httpx.Response) -> None:
        response.read()
        self.responses.append({
            "request": f"{response.request.method} {response.request.url.path}",
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.text if "json" in response.headers.get("content-type", "") else f"<{len(response.content)} bytes>",
        })


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, port: int) -> uvicorn.Server:
    # log_config=None: uvicorn's loggers, its access log included, propagate to the root handler.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="debug", access_log=True)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn didn't start")
        time.sleep(0.05)
    return server


def wait_ready(client: httpx.Client, session_id: str) -> None:
    for _ in range(500):
        if client.get(f"/sessions/{session_id}").json().get("status") == "ready":
            return
        time.sleep(0.02)
    raise RuntimeError("the document never became ready")


def upload(client: httpx.Client, pdf: Path, key: str | None) -> httpx.Response:
    headers = {"X-Gemini-Key": key} if key else {}
    with pdf.open("rb") as handle:
        return client.post("/sessions", files={"file": (pdf.name, handle, "application/pdf")}, headers=headers)


def answer(client: httpx.Client, session_id: str) -> httpx.Response:
    return client.post(f"/sessions/{session_id}/answers", json={"text": "Because thirty respondents rated it."})


def expect(response: httpx.Response, status: int, code: str | None = None) -> dict:
    body = response.json()
    if response.status_code != status or (code is not None and body.get("code") != code):
        raise AssertionError(f"{response.request.method} {response.request.url.path}: {response.status_code} {body}")
    return body


def fake_pass(pdf: Path, recorder: Recorder, dumps: list[str], upload_dir: Path) -> None:
    gemini, checker = FakeGemini(), FakeKeyChecker()
    checker.accepted.add(SENTINEL)
    settings = Settings(_env_file=None, upload_dir=upload_dir, cors_origins="http://localhost:5173")
    app = create_app(
        settings, embedder_factory=lambda _s: FakeEmbedder(), quota_store_factory=lambda _s: InMemoryQuotaStore(),
        providers_factory=lambda _s: gemini.providers, key_checker=checker,
    )
    port = free_port()
    server = serve(app, port)
    hooks = {"request": [recorder.hook_request], "response": [recorder.hook_response]}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", event_hooks=hooks, timeout=30) as client:
            expect(client.get("/status"), 200)
            preflight = client.options("/sessions", headers={
                "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-gemini-key"})
            expect_status(preflight, 200)
            expect(client.post("/keys/check", headers={"X-Gemini-Key": WRONG_KEY}), 400, "invalid_key")
            expect(client.post("/keys/check", headers={"X-Gemini-Key": SENTINEL}), 200)
            expect(upload(client, pdf, WRONG_KEY), 400, "invalid_key")
            session_id = expect(upload(client, pdf, SENTINEL), 202)["session_id"]
            wait_ready(client, session_id)
            expect(client.get(f"/sessions/{session_id}"), 200)
            expect(client.post(f"/sessions/{session_id}/start"), 200)
            expect(client.get(f"/sessions/{session_id}/current"), 200)

            gemini.fail_next("scoring", LLMQuotaExhaustedError("429 RESOURCE_EXHAUSTED", model="gemini-3.1-flash-lite"))
            expect(answer(client, session_id), 429, "own_key_limited")
            expect(answer(client, session_id), 200)
            gemini.fail_next("scoring", _rejected())
            expect(answer(client, session_id), 403, "own_key_rejected")
            done = False
            while not done:
                done = expect(answer(client, session_id), 200)["done"]
                expect(client.get(f"/sessions/{session_id}/current"), 200)
            expect(client.get(f"/sessions/{session_id}/report"), 200)
            expect(client.get(f"/sessions/{session_id}"), 200)
            quota = expect(client.get("/quota"), 200)
            assert quota["used"] == 0, quota

            session = app.state.store.get(session_id)
            dumps.append(f"[fake] settings.model_dump_json(): {settings.model_dump_json()}")
            dumps.append(f"[fake] repr(settings): {settings!r}")
            dumps.append(f"[fake] repr(session): {session!r}")
            dumps.append(f"[fake] repr(defense): {session.defense!r}"[:20000])
            dumps.append(f"[fake] repr(defense.providers): {session.defense.providers!r}")
            assert gemini.own_keys == [SENTINEL], gemini.own_keys  # the key did reach the providers
            expect_status(client.delete(f"/sessions/{session_id}"), 204)
            dumps.append(f"[fake] session.gemini_key after End session: {session.gemini_key!r}")
    finally:
        server.should_exit = True


def expect_status(response: httpx.Response, status: int) -> None:
    if response.status_code != status:
        raise AssertionError(f"{response.request.url.path}: {response.status_code}")


def _rejected() -> LLMProviderError:
    from google.genai import errors as genai_errors

    api_error = genai_errors.ClientError(
        400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}
    )
    try:
        raise LLMProviderError(f"Gemini API error, retried once and failed again. (retry: {api_error!r})") from api_error
    except LLMProviderError as exc:
        return exc


def real_client_pass(pdf: Path, recorder: Recorder, dumps: list[str], upload_dir: Path) -> None:
    from backend.app import _check_key_with_google

    checker = FakeKeyChecker()
    checker.accepted.add(SENTINEL)  # let the upload through, so /start reaches Google with the sentinel
    settings = Settings(_env_file=None, upload_dir=upload_dir, GEMINI_API_KEY=SERVER_KEY, closing_lines="off")
    app = create_app(
        settings, embedder_factory=lambda _s: FakeEmbedder(), quota_store_factory=lambda _s: InMemoryQuotaStore(),
        providers_factory=gemini_providers_factory, key_checker=checker,
    )
    port = free_port()
    server = serve(app, port)
    hooks = {"request": [recorder.hook_request], "response": [recorder.hook_response]}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", event_hooks=hooks, timeout=180) as client:
            session_id = expect(upload(client, pdf, SENTINEL), 202)["session_id"]
            wait_ready(client, session_id)
            body = expect(client.post(f"/sessions/{session_id}/start"), 403, "own_key_rejected")
            dumps.append(f"[real-client] /start: {body}")
            expect_status(client.delete(f"/sessions/{session_id}"), 204)
        # v1's real models.list probe, as production's /keys/check runs it.
        logging.getLogger("m17_leak_check").info("real models.list probe accepted=%s", _check_key_with_google(SENTINEL))
    finally:
        server.should_exit = True


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "log.txt"
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG)

    recorder, dumps = Recorder(), []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = make_pdf(Path(tmp) / "Library System.pdf", pages=16)
        for name, run in (("fake", fake_pass), ("real-client", real_client_pass)):
            logging.getLogger("m17_leak_check").info("pass %s", name)
            run(pdf, recorder, dumps, Path(tmp) / f"uploads-{name}")
            time.sleep(0.5)  # let uvicorn's shutdown lines land in the log
    handler.flush()

    (out_dir / "responses.json").write_text(json.dumps(recorder.responses, indent=1), encoding="utf-8")
    (out_dir / "dumps.txt").write_text("\n".join(dumps) + "\n", encoding="utf-8")
    (out_dir / "requests-sent.txt").write_text("\n".join(recorder.sent) + "\n", encoding="utf-8")
    print(f"log lines: {len(log_path.read_text(encoding='utf-8').splitlines())}")
    print(f"responses: {len(recorder.responses)}")
    print(f"dump lines: {len(dumps)}")
    print(f"requests sent: {len(recorder.sent)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "evidence" / "v2.0-m17-leak")
