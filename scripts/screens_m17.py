"""v2.0 M17: browser checks for the own-key control and "How it works", fully offline.

    <python with playwright> scripts/screens_m17.py [--out frontend/.screens/m17]

Run against a build made with VITE_API_BASE_URL=http://api.test (the script starts and
stops `vite preview` itself). The API is scripts/screens_m10.py's canned AniCheck
responses plus /keys/check; no backend, Gemini, MiMo or quota is used. Real Chrome, but
Playwright's routing skips CORS preflights; the backend tests cover the header's. Prints one line per check
and exits 1 if any fails.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, Request, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screens_m10 as m10  # noqa: E402

API = m10.API
KEY = "AIzaSyM17-browser-check-key-91fd"
LEAD = "Today's free defenses are used. Use your own free Gemini key to keep practicing."

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


class KeyApi(m10.Api):
    """M10's canned API, with /keys/check and a preflight that allows the key header,
    as the backend's CORS does (M17). It keeps every request, for header checks."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[Request] = []

    def handle(self, route) -> None:
        request = route.request
        self.requests.append(request)
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers={
                "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, DELETE",
                "Access-Control-Allow-Headers": "Content-Type, X-Gemini-Key",
            })
            return
        super().handle(route)


def api_for(remaining: int) -> KeyApi:
    api = KeyApi()
    for route, reply in m10.base_api(remaining).replies.items():
        api.replies[route] = reply
    api.set("POST /keys/check", {"ok": True})
    return api


def fits(page: Page) -> tuple[bool, str]:
    wide = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    return wide[0] <= wide[1], f"scrollWidth={wide[0]} innerWidth={wide[1]}"


def key_kept(page: Page) -> list[str]:
    return page.evaluate(
        """(key) => {
          const hits = [];
          for (const [name, s] of [['localStorage', localStorage], ['sessionStorage', sessionStorage]])
            for (let i = 0; i < s.length; i++) if ((s.key(i) + s.getItem(s.key(i))).includes(key)) hits.push(name);
          if (location.href.includes(key)) hits.push('URL');
          if (document.documentElement.outerHTML.includes(key)) hits.push('DOM');
          return hits;
        }""",
        KEY,
    )


def open_page(browser, url: str, width: int, api: KeyApi) -> Page:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce", device_scale_factor=1)
    page = context.new_page()
    page.route(f"{API}/**", api.handle)
    page.goto(url)
    page.get_by_text("Drop your PDF here").wait_for()
    return page


def run(browser, url: str, out: Path) -> None:
    for width in (1440, 390):
        api = api_for(8)
        page = open_page(browser, url, width, api)
        page.get_by_text("8 of 11 defenses left today").wait_for()
        header = page.locator("header")
        check(f"{width} header has How it works, the quota line and the key toggle",
              all(header.get_by_text(t).count() == 1 for t in ("How it works", "8 of 11 defenses left today", "Use your own Gemini key")))
        check(f"{width} key control collapsed by default", page.get_by_label("Your Gemini API key").count() == 0)
        ok, detail = fits(page)
        check(f"{width} start screen fits", ok, detail)
        page.screenshot(path=str(out / f"start-{width}.png"), full_page=True)

        page.get_by_role("link", name="How it works").click()
        page.wait_for_timeout(300)
        # The section's heading in view, at the top unless the page ends first (desktop's is short).
        top, at_end = page.evaluate(
            """() => [document.getElementById('how-it-works').getBoundingClientRect().top,
                      Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight]"""
        )
        check(f"{width} header link scrolls to the section", page.evaluate("scrollY") > 0 and 0 <= top < 900 and (top < 120 or at_end),
              f"section top {top:.0f}px, scrollY {page.evaluate('scrollY')}, page end reached: {at_end}")
        check(f"{width} nothing added to the URL", page.url == url, page.url)
        page.evaluate("scrollTo(0, 0)")

        header.get_by_role("button", name="Use your own Gemini key").click()
        page.get_by_label("Your Gemini API key").fill(KEY)
        ok, detail = fits(page)
        check(f"{width} key panel open fits", ok, detail)
        page.screenshot(path=str(out / f"key-open-{width}.png"), full_page=False)
        page.get_by_role("button", name="Use this key").click()
        page.get_by_text("Using your own key").wait_for()
        checks = [r for r in api.requests if r.url.endswith("/keys/check")]
        # Playwright's routing answers cross-origin requests without Chrome's CORS preflight,
        # so the preflight for X-Gemini-Key is checked in the backend tests, not here.
        post = [r for r in checks if r.method == "POST"]
        check(f"{width} /keys/check sent the key in its header only",
              len(post) == 1 and post[0].headers.get("x-gemini-key") == KEY and KEY not in post[0].url and not post[0].post_data)
        check(f"{width} key kept nowhere after entry", key_kept(page) == [], str(key_kept(page)))

        m10.upload(page, api)
        page.get_by_text("Your document is ready").wait_for(timeout=15000)
        sent = [r for r in api.requests if r.method == "POST" and r.url.endswith("/sessions")]
        body = sent[0].post_data_buffer if sent else b""
        check(f"{width} upload sent the key in its header, not the form",
              len(sent) == 1 and sent[0].headers.get("x-gemini-key") == KEY and KEY.encode() not in body)
        page.get_by_role("radio", name="No voice").check()
        page.get_by_role("button", name="Start the defense").click()
        page.locator("[data-question]").wait_for()
        check(f"{width} How it works hidden during a defense",
              page.get_by_role("region", name="How it works").count() == 0 and page.get_by_role("link", name="How it works").count() == 0)
        check(f"{width} key kept nowhere during the defense", key_kept(page) == [], str(key_kept(page)))
        page.context.close()

        api = api_for(0)
        page = open_page(browser, url, width, api)
        page.get_by_text(LEAD).wait_for()
        check(f"{width} used up: the key panel leads, open", page.get_by_label("Your Gemini API key").count() == 1)
        ok, detail = fits(page)
        check(f"{width} used-up start screen fits", ok, detail)
        page.screenshot(path=str(out / f"used-up-{width}.png"), full_page=False)
        page.context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=m10.FRONTEND / ".screens" / "m17")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    port = m10.free_port()
    preview = subprocess.Popen(
        f"npx vite preview --port {port} --strictPort", cwd=m10.FRONTEND, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            run(browser, f"http://localhost:{port}/", args.out)
            browser.close()
    finally:
        subprocess.run(f"taskkill /F /T /PID {preview.pid}", shell=True, capture_output=True)
    print(f"{len(failures)} check(s) failed" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
