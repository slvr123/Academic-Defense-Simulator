"""v2.0 M10 step 10: the visual-pass screenshots, fully offline.

    <python with playwright> scripts/screens_m10.py [--out frontend/.screens/m10]

Builds nothing: run it against `vite preview` of a build made with
VITE_API_BASE_URL=http://api.test (the script starts and stops the preview itself
from frontend/dist). Every API call is answered by Playwright from the recorded
AniCheck defense (frontend/public/demo/anicheck-defense.json), so no backend, Gemini,
MiMo or quota is used. Each state is captured at 1280 and 375 px, with motion and with
prefers-reduced-motion, and the script prints, per capture, whether the page scrolls
sideways (scrollWidth > innerWidth). Exit status 1 if any 375 px capture does.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, Route, sync_playwright

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
RECORDING = json.loads((FRONTEND / "public" / "demo" / "anicheck-defense.json").read_text(encoding="utf-8"))
SAMPLE = FRONTEND / "public" / "sample" / "sample-capstone-anicheck.pdf"
API = "http://api.test"
SID = "rec"
WIDTHS = (1280, 375)

OPTIONS = json.loads((FRONTEND / "src" / "test" / "panelOptions.real.json").read_text(encoding="utf-8"))
QUOTA = {"used": 3, "cap": 11, "remaining": 8, "resets_at": "2026-10-30T00:00:00-07:00"}
READY = RECORDING["session"]


class Api:
    """Answers http://api.test/* from canned replies; `hold` leaves a route unanswered."""

    def __init__(self) -> None:
        self.replies: dict[str, Callable[[], dict | None]] = {}
        self.held: list[Route] = []

    def set(self, route: str, body: object, status: int = 200) -> None:
        self.replies[route] = lambda: {"status": status, "body": body}

    def hold(self, route: str) -> None:
        self.replies[route] = lambda: None

    def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":  # CORS preflight for the JSON POSTs
            route.fulfill(status=204, headers={
                "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, DELETE",
                "Access-Control-Allow-Headers": "Content-Type",
            })
            return
        key = f"{request.method} {request.url.removeprefix(API)}"
        reply = self.replies.get(key)
        if reply is None:
            route.fulfill(status=404, json={"detail": f"no canned reply for {key}"})
            return
        answer = reply()
        if answer is None:
            self.held.append(route)  # frozen in flight
            return
        headers = {"Access-Control-Allow-Origin": "*"}
        if answer["status"] == 204:
            route.fulfill(status=204, headers=headers)
        else:
            route.fulfill(status=answer["status"], json=answer["body"], headers=headers)


def base_api(remaining: int = 8) -> Api:
    api = Api()
    api.set("GET /status", {"status": "ok", "model_loaded": True, "tts_available": True})
    api.set("GET /quota", {**QUOTA, "remaining": remaining, "used": 11 - remaining})
    api.set("GET /panel-options", OPTIONS)
    api.set(f"GET /sessions/{SID}", READY)
    api.set(f"POST /sessions/{SID}/start", RECORDING["start"])
    api.set(f"DELETE /sessions/{SID}", None, 204)
    return api


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- the flows: each takes a page on the start screen and leaves it in one state ---


def upload(page: Page, api: Api) -> None:
    api.replies["POST /sessions"] = lambda: {"status": 202, "body": {"session_id": SID, "status": "queued", "queue_position": 1}}
    page.set_input_files('input[type="file"]', str(SAMPLE))
    page.get_by_role("button", name="Capstone").click()
    page.get_by_role("button", name="Upload and convene the panel").click()


def to_ready(page: Page, api: Api) -> None:
    upload(page, api)
    page.get_by_text("Your document is ready").wait_for(timeout=15000)


def to_question(page: Page, api: Api) -> None:
    to_ready(page, api)
    page.get_by_role("radio", name="No voice").check()
    page.get_by_role("button", name="Start the defense").click()
    page.locator("[data-question]").wait_for()


def answer_one(page: Page, api: Api) -> None:
    to_question(page, api)
    api.set(f"POST /sessions/{SID}/answers", RECORDING["turns"][0]["response"])
    page.get_by_label("Your answer").fill(RECORDING["turns"][0]["answer"])
    page.get_by_role("button", name="Submit answer").click()
    page.get_by_role("complementary", name="Source passage").wait_for()


def report(page: Page, api: Api) -> None:
    to_question(page, api)
    api.set(f"POST /sessions/{SID}/answers", {**RECORDING["turns"][-1]["response"]})
    api.set(f"GET /sessions/{SID}/report", RECORDING["report"])
    page.get_by_label("Your answer").fill(RECORDING["turns"][-1]["answer"])
    page.get_by_role("button", name="Submit answer").click()
    page.get_by_text("Your debrief").wait_for()


def demo(page: Page, steps: int) -> None:
    page.get_by_role("button", name="Read a recorded defense").click()
    for _ in range(steps):
        page.get_by_role("button", name="Next").click()
    page.wait_for_timeout(200)


STATES: list[tuple[str, Callable[[Page, Api], None]]] = [
    ("01-start", lambda p, a: p.get_by_role("button", name="Recommended for a thesis").wait_for()),
    ("02-start-file-capstone", lambda p, a: (p.set_input_files('input[type="file"]', str(SAMPLE)), p.get_by_role("button", name="Capstone").click())),
    ("03-start-custom", lambda p, a: (p.get_by_role("button", name="Custom").click())),
    ("04-start-other-certification", lambda p, a: (p.get_by_role("button", name="Other").click(), p.get_by_label("Kind of panel").select_option("certification_interview"))),
    ("05-start-optional-details", lambda p, a: p.get_by_text("Optional details").click()),
    ("06-uploading", lambda p, a: (a.hold("POST /sessions"), p.set_input_files('input[type="file"]', str(SAMPLE)), p.get_by_role("button", name="Upload and convene the panel").click(), p.wait_for_timeout(300))),
    ("07-embedding", lambda p, a: (a.set(f"GET /sessions/{SID}", {"status": "embedding", "progress": 0.4}), upload(p, a), p.get_by_text("Reading your document").wait_for(timeout=15000))),
    ("08-ready", to_ready),
    ("09-preparing", lambda p, a: (a.hold(f"POST /sessions/{SID}/start"), to_ready(p, a), p.get_by_role("radio", name="No voice").check(), p.get_by_role("button", name="Start the defense").click(), p.get_by_text("The panel is reading your paper").wait_for())),
    ("10-question-1", to_question),
    ("11-question-typed", lambda p, a: (to_question(p, a), p.get_by_label("Your answer").fill(RECORDING["turns"][0]["answer"]))),
    ("12-reviewing", lambda p, a: (to_question(p, a), a.hold(f"POST /sessions/{SID}/answers"), p.get_by_label("Your answer").fill(RECORDING["turns"][0]["answer"]), p.get_by_role("button", name="Submit answer").click(), p.wait_for_timeout(300))),
    ("13-question-2-reveal", answer_one),
    ("14-pdf-open", lambda p, a: (answer_one(p, a), p.get_by_role("button", name="Open at page").click(), p.wait_for_timeout(800))),
    ("15-transcript", lambda p, a: (answer_one(p, a), p.get_by_role("button", name="Transcript · 1 earlier turn").click())),
    ("16-voice-menu", lambda p, a: (to_question(p, a), p.get_by_role("button", name="Voice: No voice. Change voice").click())),
    ("17-voice-disclosure", lambda p, a: (to_question(p, a), p.get_by_role("button", name="Voice: No voice. Change voice").click(), p.get_by_role("radio", name="Panel voice").check())),
    ("18-end-confirm", lambda p, a: (to_question(p, a), p.get_by_role("button", name="End session").click())),
    ("19-finishing", lambda p, a: (to_question(p, a), a.set(f"POST /sessions/{SID}/answers", RECORDING["turns"][-1]["response"]), a.hold(f"GET /sessions/{SID}/report"), p.get_by_label("Your answer").fill("x"), p.get_by_role("button", name="Submit answer").click(), p.get_by_text("The panel is writing its report").wait_for())),
    ("20-report", report),
    ("21-report-print", lambda p, a: (report(p, a), p.emulate_media(media="print"), p.evaluate("window.dispatchEvent(new Event('beforeprint'))"))),
    ("22-failed-scanned", lambda p, a: (a.set(f"GET /sessions/{SID}", {"status": "failed", "error": "x", "error_code": "scanned_pdf"}), upload(p, a), p.get_by_text("This document couldn't be read").wait_for(timeout=15000))),
    ("23-demo-preparing", lambda p, a: demo(p, 0)),
    ("24-demo-question-1", lambda p, a: demo(p, 1)),
    ("25-demo-question-3", lambda p, a: demo(p, 3)),
    ("26-demo-report", lambda p, a: demo(p, len(RECORDING["turns"]) + 1)),
    ("27-quota-zero", lambda p, a: p.get_by_text("No defenses left today").wait_for()),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=FRONTEND / ".screens" / "m10")
    parser.add_argument("--only", help="capture only states whose name contains this")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    preview = subprocess.Popen(
        f"npx vite preview --port {port} --strictPort", cwd=FRONTEND, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    overflowing: list[str] = []
    try:
        url = f"http://localhost:{port}/"
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            for name, flow in STATES:
                if args.only and args.only not in name:
                    continue
                for width in WIDTHS:
                    for motion in ("motion", "reduced"):
                        context = browser.new_context(
                            viewport={"width": width, "height": 900},
                            reduced_motion="reduce" if motion == "reduced" else "no-preference",
                            device_scale_factor=1,
                        )
                        page = context.new_page()
                        api = base_api(remaining=0 if name == "27-quota-zero" else 8)
                        page.route(f"{API}/**", api.handle)
                        page.goto(url)
                        page.get_by_text("Drop your PDF here").wait_for()
                        flow(page, api)
                        page.wait_for_timeout(1200 if motion == "motion" else 150)  # timelines settle
                        wide = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
                        file = args.out / f"{name}-{width}-{motion}.png"
                        page.screenshot(path=str(file), full_page=True)
                        flag = "OVERFLOW" if wide[0] > wide[1] else "fits"
                        if wide[0] > wide[1] and width == 375:
                            overflowing.append(file.name)
                        print(f"{file.name:48} scrollWidth={wide[0]} innerWidth={wide[1]} {flag}")
                        context.close()
            browser.close()
    finally:
        subprocess.run(f"taskkill /F /T /PID {preview.pid}", shell=True, capture_output=True)
    print(f"{len(overflowing)} capture(s) at 375 px scroll sideways" + (f": {overflowing}" if overflowing else ""))
    return 1 if overflowing else 0


if __name__ == "__main__":
    sys.exit(main())
