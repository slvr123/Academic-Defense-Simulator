"""M17's one live check: an own-key defense against the deployed backend.

    python scripts/m17_live_own_key.py --base-url https://ads-backend-bv63.onrender.com

The key is the dev project's GEMINI_API_KEY from backend/.env, read here through
backend.settings and sent only in the X-Gemini-Key header. It's never printed or
written: the summary file holds statuses, counts and timings only.

It reads /quota before and after, checks the key, uploads the bundled sample capstone
with the key, runs the defense to its report (answers built from each question's own
passage, as scripts/e2e_defense.py does), and ends the session. An own-key defense
reserves no free slot, so `used` must be the same after as before, unless a visitor
started a free defense meanwhile (the script says so if `used` moved).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.settings import Settings  # noqa: E402
from scripts.e2e_defense import auto_answer  # noqa: E402

SAMPLE = REPO_ROOT / "frontend" / "public" / "sample" / "sample-capstone-anicheck.pdf"
OUT = REPO_ROOT / "evals" / "v2.0-m17-live-own-key.json"
BUSY_RETRIES = 3


def call(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    """One request, retried after Retry-After on a 503 (the panel or the server busy)."""
    for attempt in range(BUSY_RETRIES + 1):
        response = client.request(method, path, **kwargs)
        if response.status_code != 503 or attempt == BUSY_RETRIES:
            return response
        wait = float(response.headers.get("retry-after", "30"))
        print(f"  503 on {method} {path} ({response.json().get('code')}); waiting {wait:g} s")
        time.sleep(wait)
    raise AssertionError("unreachable")


def expect(response: httpx.Response, status: int, what: str) -> dict:
    if response.status_code != status:
        body = response.text if "json" in response.headers.get("content-type", "") else f"<{len(response.content)} bytes>"
        raise SystemExit(f"{what}: HTTP {response.status_code} {body}")
    return response.json() if response.content else {}


def wake(client: httpx.Client) -> dict:
    for _ in range(60):
        try:
            response = client.get("/status")
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(5)
    raise SystemExit("the backend didn't wake in 5 minutes")


def main(base_url: str) -> None:
    secret = Settings().gemini_api_key
    if secret is None:
        raise SystemExit("backend/.env has no GEMINI_API_KEY")
    key = {"X-Gemini-Key": secret.get_secret_value()}
    summary: dict = {"base_url": base_url, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    with httpx.Client(base_url=base_url, timeout=180) as client:
        summary["status"] = wake(client)
        before = expect(call(client, "GET", "/quota"), 200, "GET /quota")
        print(f"quota before: {before}")

        expect(client.post("/keys/check", headers=key), 200, "POST /keys/check")
        print("POST /keys/check: 200 {'ok': True}")

        with SAMPLE.open("rb") as handle:
            files = {"file": (SAMPLE.name, handle, "application/pdf")}
            panel = json.dumps({"defense_type": "thesis"})
            created = expect(client.post("/sessions", files=files, data={"panel": panel}, headers=key), 202, "POST /sessions")
        session_id = created["session_id"]
        print(f"POST /sessions: 202, session {session_id[:8]}...")
        while (state := expect(client.get(f"/sessions/{session_id}"), 200, "GET session"))["status"] != "ready":
            if state["status"] == "failed":
                raise SystemExit(f"ingest failed: {state}")
            time.sleep(1)

        started = time.perf_counter()
        start = expect(call(client, "POST", f"/sessions/{session_id}/start"), 200, "POST /start")
        turns = [{"turn": 1, "panelist": start["question"]["panelist"]["title"], "page": start["question"]["sources"][0]["page_start"]}]
        print(f"POST /start: 200, panel {[p['title'] for p in start['panel']]}")
        question = start["question"]
        while True:
            body = expect(
                call(client, "POST", f"/sessions/{session_id}/answers", json={"text": auto_answer(question), "turn": question["turn"]}),
                200, f"POST /answers turn {question['turn']}",
            )
            print(f"  turn {question['turn']} answered: total {body['evaluation']['total']}/15")
            if body["done"]:
                break
            question = body["next_question"]
            turns.append({"turn": question["turn"], "panelist": question["panelist"]["title"], "page": question["sources"][0]["page_start"]})
        report = expect(client.get(f"/sessions/{session_id}/report"), 200, "GET /report")["report"]
        summary["defense_s"] = round(time.perf_counter() - started, 1)

        after = expect(call(client, "GET", "/quota"), 200, "GET /quota")
        ended = client.delete(f"/sessions/{session_id}")  # the key dies with the session
        summary["end_session_status"] = ended.status_code

    suggestions = report.get("answer_suggestions") or []
    summary.update(
        quota_before=before, quota_after=after, quota_used_unchanged=before["used"] == after["used"],
        turns=turns, suggestions_fallback_used=report["suggestions_fallback_used"], suggestions=len(suggestions),
        narrative_fallback_used=report.get("narrative_fallback_used"),
    )
    print(f"quota after:  {after}")
    print(f"free quota used: {before['used']} -> {after['used']} ({'unchanged' if summary['quota_used_unchanged'] else 'CHANGED'})")
    print(f"turns: {len(turns)}; suggestions: {len(suggestions)}; suggestions_fallback_used: {report['suggestions_fallback_used']}")
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"-> {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://ads-backend-bv63.onrender.com")
    main(parser.parse_args().base_url)
