"""Call Gemini until it answers 429, and save the full error so M2's 429 classifier
can be built from real bodies instead of guesses.

Usage (from the repo root, with backend/requirements-dev.txt installed):

    python scripts/capture_429.py gemini-3.1-flash-lite burst
    python scripts/capture_429.py gemini-2.5-flash daily

The key is GEMINI_API_KEY from backend/.env. It is never printed, and any copy of
it in a saved body is replaced with "<redacted>". Use a dev-project key: `daily`
spends that model's whole day of quota on it.

- `burst` sends small requests from several threads at once, to trip the
  per-minute limit.
- `daily` sends one request every `--interval` seconds (default 20, i.e. 3 a
  minute, under the 5 RPM of gemini-2.5-flash), to run out the per-day limit
  without tripping the per-minute one.

After each 429 it waits `--probe-wait` seconds (default 65, longer than the
per-minute window) and sends one more request. If that succeeds, the 429 was
per-minute and `daily` carries on; if it 429s too, the limit outlasted a minute
and the run stops. Every 429 is saved with that probe outcome, which labels it.

Output: captures/429-<model>-<mode>-<UTC time>.json (gitignored). Each record has
the HTTP code, status, message, the full error JSON (`details`, where Google puts
any quota metadata) and the response headers.

This calls google-genai directly rather than through GeminiProvider: the
provider's per-minute limiter exists to prevent exactly the 429 `burst` needs.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "backend" / ".env"
CAPTURES = REPO_ROOT / "captures"
PROMPT = "Reply with the single word OK."


def load_key(env_file: Path = ENV_FILE) -> str:
    key = (dotenv_values(env_file).get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise SystemExit(f"GEMINI_API_KEY is not set in {env_file}")
    return key


def redact(value: Any, secret: str) -> Any:
    """`value` with every occurrence of `secret` in its strings replaced."""
    if isinstance(value, str):
        return value.replace(secret, "<redacted>")
    if isinstance(value, dict):
        return {redact(k, secret): redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    return value


def error_record(exc: genai_errors.APIError, *, call: int, elapsed_s: float, secret: str) -> dict:
    headers = dict(exc.response.headers) if getattr(exc.response, "headers", None) is not None else None
    return redact(
        {
            "call": call,
            "elapsed_s": round(elapsed_s, 2),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "code": exc.code,
            "status": exc.status,
            "message": exc.message,
            "details": exc.details,
            "headers": headers,
        },
        secret,
    )


class Capture:
    def __init__(self, client: genai.Client, model: str, secret: str) -> None:
        self.client = client
        self.model = model
        self.secret = secret
        self.started = time.perf_counter()
        self.calls = 0
        self.successes = 0
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def call(self) -> genai_errors.APIError | None:
        """One request. None on success, the APIError on any API error."""
        with self._lock:
            self.calls += 1
        try:
            self.client.models.generate_content(
                model=self.model, contents=PROMPT, config=types.GenerateContentConfig(max_output_tokens=8)
            )
        except genai_errors.APIError as exc:
            return exc
        with self._lock:
            self.successes += 1
        return None

    def record(self, exc: genai_errors.APIError) -> dict:
        record = error_record(exc, call=self.calls, elapsed_s=time.perf_counter() - self.started, secret=self.secret)
        self.records.append(record)
        return record

    def probe_after(self, record: dict, wait_s: float) -> bool:
        """Waits, sends one request, stores the outcome on `record`. True if it 429'd again."""
        print(f"  429 after {self.successes} successful call(s); waiting {wait_s:g} s, then one probe request")
        time.sleep(wait_s)
        exc = self.call()
        if exc is None:
            record["probe_after_wait"] = {"wait_s": wait_s, "outcome": "ok"}
            print("  probe: ok, so that 429 was per-minute")
            return False
        if exc.code != 429:
            raise exc
        record["probe_after_wait"] = {"wait_s": wait_s, "outcome": "429"}
        self.record(exc)["note"] = "the probe request after the wait"
        print("  probe: 429 again, so the limit outlasted a minute")
        return True


def run_burst(capture: Capture, *, workers: int, max_calls: int, probe_wait: float) -> None:
    tripped = threading.Event()
    first: list[genai_errors.APIError] = []

    def one() -> None:
        if tripped.is_set():
            return
        exc = capture.call()
        if exc is not None:
            with capture._lock:
                first.append(exc)
            tripped.set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: set[Future] = set()
        submitted = 0
        while not tripped.is_set() and (submitted < max_calls or pending):
            while submitted < max_calls and len(pending) < workers and not tripped.is_set():
                pending.add(pool.submit(one))
                submitted += 1
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
        wait(pending)
    if not first:
        return
    exc = first[0]
    if exc.code != 429:
        raise exc
    capture.probe_after(capture.record(exc), probe_wait)


def run_daily(capture: Capture, *, interval: float, max_calls: int, probe_wait: float) -> None:
    while capture.calls < max_calls:
        exc = capture.call()
        if exc is None:
            print(f"  call {capture.calls}: ok ({capture.successes} so far)")
            time.sleep(interval)
            continue
        if exc.code != 429:
            raise exc
        if capture.probe_after(capture.record(exc), probe_wait):
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model")
    parser.add_argument("mode", choices=["burst", "daily"])
    parser.add_argument("--workers", type=int, default=8, help="burst only: requests in flight at once")
    parser.add_argument("--interval", type=float, default=20.0, help="daily only: seconds between requests")
    parser.add_argument("--max-calls", type=int, default=None, help="default: 60 for burst, 40 for daily")
    parser.add_argument("--probe-wait", type=float, default=65.0, help="seconds to wait after a 429 before probing")
    args = parser.parse_args(argv)
    max_calls = args.max_calls or (60 if args.mode == "burst" else 40)

    secret = load_key()
    client = genai.Client(api_key=secret, http_options=types.HttpOptions(timeout=30_000))
    capture = Capture(client, args.model, secret)
    started_at = datetime.now(timezone.utc)
    print(f"{args.model} {args.mode}: up to {max_calls} calls")
    try:
        if args.mode == "burst":
            run_burst(capture, workers=args.workers, max_calls=max_calls, probe_wait=args.probe_wait)
        else:
            run_daily(capture, interval=args.interval, max_calls=max_calls, probe_wait=args.probe_wait)
        error = None
    except genai_errors.APIError as exc:  # not a 429: save it too, it's still a real body
        print(f"  stopped on a non-429 error: {exc.code} {exc.status}")
        error = error_record(exc, call=capture.calls, elapsed_s=time.perf_counter() - capture.started, secret=secret)

    CAPTURES.mkdir(exist_ok=True)
    out = CAPTURES / f"429-{args.model}-{args.mode}-{started_at:%Y%m%dT%H%M%SZ}.json"
    out.write_text(
        json.dumps(
            {
                "model": args.model,
                "mode": args.mode,
                "started_at": started_at.isoformat(),
                "calls": capture.calls,
                "successes": capture.successes,
                "records": capture.records,
                "non_429_error": error,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"{len(capture.records)} 429 record(s), {capture.successes} successful call(s); saved {out}")
    return 0 if capture.records else 1


if __name__ == "__main__":
    sys.exit(main())
