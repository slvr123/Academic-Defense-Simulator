"""v2.0 M12: browser evidence for the header (D2), the glow removal (D3), the themes (D4)
and the cold-start behaviour (D5), fully offline.

    <python with playwright> scripts/screens_m12.py [--out frontend/.screens/m12]

Run against a build made with VITE_API_BASE_URL=http://api.test (the script starts and
stops `vite preview` from frontend/dist itself). API calls are answered from
scripts/screens_m10.py's canned AniCheck responses; no backend, Gemini, MiMo or quota is
used. Prints one line per check and exits 1 if any fails:

  header   the wordmark and the turn status share a baseline; the status sits at the right
           edge on desktop and left-aligned (never centred) when it wraps on a phone;
           screenshots in both themes at 1280 and 375 px
  glow     no element on the session screen, the lit card and its portrait included, has a
           computed box-shadow, text-shadow, or a blur/drop-shadow filter, while the lit
           panelist is speaking (a stand-in speechSynthesis keeps the browser voice speaking)
  demo     the recorded demo, stepped from the start screen to its debrief, sends no
           request to the backend, and still finishes with every backend call refused
  waking   with /status held unanswered, the waking line appears after 3 s, not before,
           and the start screen stays usable
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screens_m10 as m10  # noqa: E402

API = m10.API
DEMO = json.loads((m10.FRONTEND / "public" / "demo" / "anicheck-defense.json").read_text(encoding="utf-8"))
WAKING_TEXT = "Waking the panel - free hosting sleeps when idle. This can take up to a minute."

# The browser voice "speaks" until the page goes: onstart fires, onend never does.
SPEAKING_VOICE = """
window.speechSynthesis.speak = (u) => { setTimeout(() => u.onstart && u.onstart(new Event('start')), 50) };
"""

# A zero-size inline-block sits on its line's baseline: its top is the baseline's y.
BASELINE = """(el) => {
  const probe = document.createElement('span');
  probe.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
  el.appendChild(probe);
  const y = probe.getBoundingClientRect().top;
  probe.remove();
  return y;
}"""

GLOW_SCAN = """() => {
  const hits = [];
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    const filter = s.filter.includes('blur') || s.filter.includes('drop-shadow');
    if (s.boxShadow !== 'none' || s.textShadow !== 'none' || filter) {
      hits.push(`${el.tagName.toLowerCase()}.${(el.getAttribute('class') || '').slice(0, 60)} box=${s.boxShadow} text=${s.textShadow} filter=${s.filter}`);
    }
  }
  return hits;
}"""

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    if not ok:
        failures.append(name)


def new_page(browser: Browser, url: str, width: int, theme: str, api: m10.Api | None) -> Page:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    if theme == "chamber":
        context.add_init_script("localStorage.setItem('ads-theme', 'chamber')")
    page = context.new_page()
    if api is not None:
        page.route(f"{API}/**", api.handle)
    page.goto(url)
    page.get_by_text("Drop your PDF here").wait_for()
    return page


def header_checks(browser: Browser, url: str, out: Path) -> None:
    for theme in ("paper", "chamber"):
        for width in m10.WIDTHS:
            api = m10.base_api()
            page = new_page(browser, url, width, theme, api)
            m10.to_question(page, api)
            page.wait_for_timeout(200)
            attr = page.evaluate("document.documentElement.dataset.theme || '(none: Paper)'")
            title = page.locator("header h1")
            status = page.locator("[data-turn-status]")
            row = page.locator("[data-header-row]")
            title_y, status_y = title.evaluate(BASELINE), status.evaluate(BASELINE)
            t, s, r = title.bounding_box(), status.bounding_box(), row.bounding_box()
            assert t and s and r
            same_line = abs(t["y"] - s["y"]) < t["height"]
            text = status.inner_text()
            if same_line:
                gap_right = (r["x"] + r["width"]) - (s["x"] + s["width"])
                check(f"header {theme} {width}", abs(title_y - status_y) <= 1 and gap_right <= 1,
                      f"data-theme={attr}; '{text}' on the title's line, baselines {title_y:.1f}/{status_y:.1f}, "
                      f"{gap_right:.1f}px from the right edge")
            else:
                check(f"header {theme} {width}", abs(s["x"] - r["x"]) <= 1,
                      f"data-theme={attr}; '{text}' wrapped under the title, left edge {s['x']:.1f} = row {r['x']:.1f} (left-aligned)")
            wide = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
            check(f"no sideways scroll {theme} {width}", wide[0] <= wide[1], f"scrollWidth={wide[0]} innerWidth={wide[1]}")
            page.screenshot(path=str(out / f"header-{theme}-{width}.png"), clip={"x": 0, "y": 0, "width": width, "height": 520 if width < 600 else 420})
            page.screenshot(path=str(out / f"question-{theme}-{width}.png"), full_page=True)
            page.context.close()


def glow_checks(browser: Browser, url: str, out: Path) -> None:
    for theme in ("paper", "chamber"):
        context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="no-preference")
        context.add_init_script(SPEAKING_VOICE)
        if theme == "chamber":
            context.add_init_script("localStorage.setItem('ads-theme', 'chamber')")
        page = context.new_page()
        api = m10.base_api()
        page.route(f"{API}/**", api.handle)
        page.goto(url)
        page.get_by_text("Drop your PDF here").wait_for()
        m10.to_ready(page, api)
        page.get_by_role("radio", name="Browser voice").check()
        page.get_by_role("button", name="Start the defense").click()
        page.locator("[data-lit]").get_by_text("Speaking").wait_for(timeout=10000)
        page.wait_for_timeout(1200)  # GSAP handoff settles
        lit = page.locator("[data-lit]")
        style = lit.evaluate("e => { const s = getComputedStyle(e); return [s.borderTopWidth, s.borderTopStyle, s.borderTopColor, s.boxShadow] }")
        portrait = lit.locator("img").evaluate("e => { const s = getComputedStyle(e); return [s.boxShadow, s.filter, s.animationName, e.dataset.speaking] }")
        hits = page.evaluate(GLOW_SCAN)
        check(f"speaking card {theme}", style[:2] == ["1px", "solid"] and style[3] == "none" and portrait[0] == "none" and portrait[2] == "none",
              f"card border {style[0]} {style[1]} {style[2]}, box-shadow {style[3]}; portrait box-shadow {portrait[0]}, "
              f"filter {portrait[1]}, animation {portrait[2]}, data-speaking={portrait[3]}")
        check(f"no glow anywhere {theme}", not hits, f"{len(hits)} element(s) with a shadow or blur" + (f": {hits[:5]}" if hits else ""))
        page.screenshot(path=str(out / f"speaking-{theme}-1280.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 360})
        context.close()


def demo_checks(browser: Browser, url: str) -> None:
    for mode in ("answered", "refused"):
        context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        page = context.new_page()
        api = m10.base_api()
        backend: list[str] = []
        page.on("request", lambda r: backend.append(f"{r.method} {r.url}") if r.url.startswith(API) else None)
        if mode == "answered":
            page.route(f"{API}/**", api.handle)
        else:
            page.route(f"{API}/**", lambda route: route.abort("connectionrefused"))
        page.goto(url)
        page.get_by_text("Drop your PDF here").wait_for()
        if mode == "answered":
            page.get_by_text("defenses left today").wait_for()  # the page-load wake and reads are done
        page.wait_for_timeout(500)
        before = len(backend)
        page.get_by_role("button", name="Read a recorded defense").click()
        # Step 0 is the preparing screen, then one per recorded turn, then the debrief.
        steps = len(DEMO["turns"]) + 1
        for _ in range(steps):
            page.get_by_role("button", name="Next", exact=True).click()
        page.get_by_text("Your debrief").wait_for()
        during = backend[before:]
        check(f"demo {mode}", not during,
              f"page load sent {before} backend request(s) {sorted(set(backend[:before]))}; the demo, {steps} Next clicks "
              f"to the debrief, sent {len(during)}" + (f": {during}" if during else ""))
        context.close()


def waking_checks(browser: Browser, url: str, out: Path) -> None:
    for theme in ("paper", "chamber"):
        context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        if theme == "chamber":
            context.add_init_script("localStorage.setItem('ads-theme', 'chamber')")
        page = context.new_page()
        api = m10.base_api()
        api.hold("GET /status")
        page.route(f"{API}/**", api.handle)
        started = time.monotonic()
        page.goto(url)
        page.get_by_text("Drop your PDF here").wait_for()
        page.wait_for_timeout(max(0, 2500 - int((time.monotonic() - started) * 1000)))
        early = page.get_by_text(WAKING_TEXT).count()
        page.get_by_text(WAKING_TEXT).wait_for(timeout=5000)
        shown_at = time.monotonic() - started
        usable = page.get_by_role("button", name="Read a recorded defense").is_enabled()
        check(f"waking {theme}", early == 0 and usable,
              f"line absent at 2.5 s ({early} found), shown by {shown_at:.1f} s after navigation; demo button enabled={usable}")
        page.screenshot(path=str(out / f"waking-{theme}-1280.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 420})
        page.unroute_all(behavior="ignoreErrors")  # the held /status, still in flight
        context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=m10.FRONTEND / ".screens" / "m12")
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
        url = f"http://localhost:{port}/"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            header_checks(browser, url, args.out)
            glow_checks(browser, url, args.out)
            demo_checks(browser, url)
            waking_checks(browser, url, args.out)
            browser.close()
    finally:
        subprocess.run(f"taskkill /F /T /PID {preview.pid}", shell=True, capture_output=True)
    print(f"{len(failures)} check(s) failed" + (f": {failures}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
