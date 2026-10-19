"""Upload one PDF to a running v2.0 backend, poll until ready or failed, print timings.

Usage (from the repo root, with backend/requirements-dev.txt installed):

    python scripts/measure_ingest.py path/to/thesis.pdf --base-url https://<svc>.onrender.com

Memory isn't reported here: the backend logs it (`memory ...` and `ingest ...` lines),
so read peak RSS from the server log for the same run. See
docs/v2.0-m1-memory-results.md for the full measurement protocol.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--panel", default=None, help="panel JSON; omitted = defaults")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--keep", action="store_true", help="don't DELETE the session afterwards")
    args = parser.parse_args()

    with fitz.open(args.pdf) as document:
        page_count = document.page_count
    size_mb = args.pdf.stat().st_size / (1024 * 1024)
    print(f"file: {args.pdf.name}  {size_mb:.2f} MB  {page_count} pages")

    # Generous timeouts: a cold Render instance can take a minute to answer.
    with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(300)) as client:
        started = time.perf_counter()
        health = client.get("/health")
        print(f"health: {health.status_code} {health.text}  ({time.perf_counter() - started:.2f}s)")

        data = {"panel": args.panel} if args.panel is not None else {}
        upload_started = time.perf_counter()
        with args.pdf.open("rb") as handle:
            response = client.post(
                "/sessions", files={"file": (args.pdf.name, handle, "application/pdf")}, data=data
            )
        upload_s = time.perf_counter() - upload_started
        print(f"upload: {response.status_code} {response.text}  ({upload_s:.2f}s)")
        if response.status_code != 202:
            return 1
        session_id = response.json()["session_id"]

        transitions: dict[str, float] = {"queued": time.perf_counter()}
        last_line = ""
        state: dict = {}
        while time.perf_counter() - upload_started < args.timeout_seconds:
            state = client.get(f"/sessions/{session_id}").json()
            status = state["status"]
            transitions.setdefault(status, time.perf_counter())
            line = f"  {status}" + (f" progress={state['progress']:.2f}" if state.get("progress") is not None else "")
            if line != last_line:
                print(f"{time.perf_counter() - upload_started:8.2f}s{line}")
                last_line = line
            if status in ("ready", "failed"):
                break
            time.sleep(args.poll_seconds)
        else:
            print("timed out waiting for the session")
            return 1

        end = transitions.get("ready") or transitions.get("failed") or time.perf_counter()
        summary = {
            "file": args.pdf.name,
            "size_mb": round(size_mb, 2),
            "pages": page_count,
            "status": state["status"],
            "chunk_count": state.get("chunk_count"),
            "error": state.get("error"),
            "upload_s": round(upload_s, 2),
            "queued_s": round(transitions.get("embedding", end) - transitions["queued"], 2),
            "embedding_s": round(end - transitions.get("embedding", end), 2),
            "upload_to_done_s": round(end - upload_started, 2),
            "session_id": session_id,
        }
        if not args.keep:
            client.delete(f"/sessions/{session_id}")
        print(json.dumps(summary, indent=2))
        return 0 if state["status"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
