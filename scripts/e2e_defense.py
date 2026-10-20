"""One full defense against a running backend: upload, start, answer every
question, fetch the report. Prints the transcript with timings and writes it all
to a JSON file.

Usage (from the repo root, with backend/requirements-dev.txt installed):

    python scripts/e2e_defense.py path/to/thesis.pdf --base-url http://127.0.0.1:8000
    python scripts/e2e_defense.py path/to/thesis.pdf --base-url https://<svc>.onrender.com --interactive

Against a real backend this spends real Gemini quota and one of today's defense
slots. Without --interactive, each answer is built from the question's own source
passage, so the run needs no typing; with it, you type the answers.

If the server has ADS_DEBUG_MEMORY=1, it also reads RSS and VmHWM before and
after /start, before and after the last answer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
BUSY_RETRIES = 3


def auto_answer(question: dict) -> str:
    """A plausible, grounded answer: the source passage's first sentences, framed as
    the candidate's reasoning. It exercises the engine without a person typing."""
    source = question["sources"][0]
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(source["text"].split()))
    evidence = " ".join(sentences[:3])[:700]
    return (
        f"As we wrote on page {source['page_start']}: {evidence} "
        "We chose this approach because it fits the problem we set out to solve, "
        "and we checked it against the objectives in our first chapter."
    )


class Run:
    def __init__(self, client: httpx.Client, log: Callable[[str], None] = print) -> None:
        self.client = client
        self.log = log
        self.memory: list[dict] = []

    def call(self, method: str, path: str, **kwargs) -> tuple[httpx.Response, float]:
        """One request, retried after Retry-After on a busy 503."""
        for attempt in range(BUSY_RETRIES + 1):
            started = time.perf_counter()
            response = self.client.request(method, path, **kwargs)
            elapsed = time.perf_counter() - started
            if response.status_code != 503 or attempt == BUSY_RETRIES:
                return response, elapsed
            wait = float(response.headers.get("retry-after", "60"))
            self.log(f"  503 {response.json().get('detail')!r}; waiting {wait:g} s")
            time.sleep(wait)
        raise AssertionError("unreachable")

    def snapshot(self, label: str) -> None:
        response = self.client.get("/debug/memory")
        if response.status_code == 200:
            body = response.json()
            self.memory.append({"at": label, "rss_mb": body["rss_mb"], "hwm_mb": body["hwm_mb"]})
            self.log(f"  memory {label}: rss {body['rss_mb']} MB, hwm {body['hwm_mb']} MB")


def expect(response: httpx.Response, status: int, what: str) -> dict:
    if response.status_code != status:
        raise SystemExit(f"{what}: HTTP {response.status_code} {response.text}")
    return response.json()


def run_defense(
    client: httpx.Client, pdf: Path, *, panel: str | None, answer_for: Callable[[dict], str],
    log: Callable[[str], None] = print, poll_s: float = 1.0,
) -> dict:
    run = Run(client, log)
    record: dict = {"pdf": pdf.name, "started_at": datetime.now(timezone.utc).isoformat(), "turns": []}
    record["quota_before"] = expect(client.get("/quota"), 200, "GET /quota")
    log(f"quota before: {record['quota_before']}")

    started = time.perf_counter()
    with pdf.open("rb") as handle:
        data = {"panel": panel} if panel else {}
        created = expect(
            client.post("/sessions", files={"file": (pdf.name, handle, "application/pdf")}, data=data),
            202, "upload",
        )
    session_id = created["session_id"]
    while (state := expect(client.get(f"/sessions/{session_id}"), 200, "poll"))["status"] not in ("ready", "failed"):
        time.sleep(poll_s)
    if state["status"] == "failed":
        raise SystemExit(f"ingest failed: {state.get('error')}")
    record["upload_to_ready_s"] = round(time.perf_counter() - started, 2)
    log(f"ready in {record['upload_to_ready_s']} s: {state['chunk_count']} chunks")

    run.snapshot("before start")
    response, elapsed = run.call("POST", f"/sessions/{session_id}/start")
    start = expect(response, 200, "start")
    run.snapshot("after start")
    record.update(start_s=round(elapsed, 2), domain=start["domain"], topic=start["topic"], panel=start["panel"])
    log(f"start in {elapsed:.1f} s; domain {start['domain']!r}, topic {start['topic']!r}")
    log("panel: " + ", ".join(f"Dr. {p['name']} ({p['title']})" for p in start["panel"]))

    question = start["question"]
    while True:
        source = question["sources"][0]
        log(
            f"\nturn {question['turn']} - Dr. {question['panelist']['name']} ({question['panelist']['title']}), "
            f"difficulty {question['difficulty']}\n  Q: {question['question']}\n"
            f"  source: pages {source['page_start']}-{source['page_end']}, score {source['score']}, {source['origin']}"
        )
        text = answer_for(question)
        response, elapsed = run.call("POST", f"/sessions/{session_id}/answers", json={"text": text})
        result = expect(response, 200, "answer")
        evaluation = result["evaluation"]
        log(
            f"  A: {text[:160]}{'...' if len(text) > 160 else ''}\n"
            f"  scored {evaluation['clarity']}/{evaluation['depth']}/{evaluation['grounding']} "
            f"(total {evaluation['total']}/15) in {elapsed:.1f} s; gap: {evaluation['primary_gap']}"
        )
        record["turns"].append({"question": question, "answer": text, "evaluation": evaluation, "answer_s": round(elapsed, 2)})
        if result["done"]:
            break
        question = result["next_question"]
    run.snapshot("after the last answer")

    report = expect(client.get(f"/sessions/{session_id}/report"), 200, "report")
    record["report"] = report
    record["total_s"] = round(time.perf_counter() - started, 2)
    record["quota_after"] = expect(client.get("/quota"), 200, "GET /quota")
    record["memory"] = run.memory
    summary = report["report"]
    log(
        f"\nreport: {len(report['transcript'])} turns, averages clarity {summary['overall_avg_clarity']:.2f}, "
        f"depth {summary['overall_avg_depth']:.2f}, grounding {summary['overall_avg_grounding']:.2f}; "
        f"narrative {'fallback' if summary['narrative_fallback_used'] else 'yes'}, "
        f"{len(summary['answer_suggestions'])} suggestions"
        f"{' (fallback)' if summary['suggestions_fallback_used'] else ''}"
    )
    log(f"quota after: {record['quota_after']}; total {record['total_s']} s")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--panel", default=None, help="panel JSON; omitted = defaults")
    parser.add_argument("--interactive", action="store_true", help="type the answers yourself")
    parser.add_argument("--out", type=Path, default=None, help="default: e2e-defense-<UTC time>.json in the cwd")
    args = parser.parse_args(argv)

    def typed(question: dict) -> str:
        while not (text := input("  your answer: ").strip()):
            pass
        return text

    out = args.out or Path(f"e2e-defense-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(300)) as client:
        record = run_defense(client, args.pdf, panel=args.panel, answer_for=typed if args.interactive else auto_answer)
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
