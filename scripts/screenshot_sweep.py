"""Screenshot sweep (v1.3a Decision 1) — DEV-ONLY standing UI check, fully offline.

Captures intake, panel selection, the recorded example and the running (defense) stage
at 390, 768, 1024 and 1440px, and prints for each:
`document.documentElement.scrollWidth` vs `innerWidth`, the same pair for Streamlit's
own scroll container (`[data-testid="stMain"]`), the main container's left/width, and the
sidebar's box and how much of the main container it covers. Any UI slice runs this as
Definition-of-Done evidence; a width passes only when BOTH pairs match.

Why both: Streamlit scrolls inside `stMain`, which clips horizontal overflow, so the
document itself never gets wider than the viewport. Measured on v1.3a's baseline, the
390px intake had its content 300px off-screen while documentElement.scrollWidth still
equalled innerWidth; only stMain.scrollWidth (690 vs 390) showed it.

Offline by construction: the app is launched from `scripts/offline_stub_app.py` (every
LLM call, the embedding model and MiMo TTS stubbed; a missed call raises instead of
reaching the network), on a fresh free port, with ADS_TTS_PROVIDER=browser and
persistence off. The demo counter file is backed up before the run and restored after
it, so evidence runs never spend real demo slots.

Dev dependency, deliberately NOT in requirements.txt (deploy is unaffected): Playwright,
installed in whichever Python runs this script, plus a Chrome install
(`channel="chrome"`, no Playwright browser download needed):

    uv venv .sweep-venv && uv pip install --python .sweep-venv/Scripts/python.exe playwright
    .sweep-venv/Scripts/python.exe scripts/screenshot_sweep.py --out <dir>

The app server itself runs on the project venv (`--app-python`, default `.venv`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
COUNTER_FILE = REPO_ROOT / "academic_defense_simulator" / "data" / "demo_counter.json"
STUB_APP = REPO_ROOT / "scripts" / "offline_stub_app.py"
DEFAULT_WIDTHS = (390, 768, 1024, 1440)
MEASURE_HEIGHT = 844  # a phone-shaped viewport for the measurements themselves
EXAMPLE_BUTTON = re.compile(r"recorded (example|defense)", re.IGNORECASE)

_METRICS_JS = """
() => {
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width)};
  };
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.right > 0
      && r.left < innerWidth && r.top < innerHeight && r.bottom > 0;
  };
  const main = document.querySelector('.stMainBlockContainer');
  const scroller = document.querySelector('[data-testid="stMain"]');
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  const mainBox = box(main);
  const sideBox = visible(sidebar) ? box(sidebar) : null;
  let covered = 0;
  if (mainBox && sideBox) {
    const overlap = Math.min(sideBox.right, mainBox.right) - Math.max(sideBox.left, mainBox.left);
    covered = Math.max(0, overlap) / mainBox.width;
  }
  const collapse = document.querySelector('[data-testid="stSidebarCollapseButton"] button')
    || document.querySelector('[data-testid="stSidebarCollapseButton"]');
  return {
    innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    innerScrollWidth: scroller ? scroller.scrollWidth : null,
    innerClientWidth: scroller ? scroller.clientWidth : null,
    main: mainBox,
    sidebar: sideBox,
    sidebarCoversMain: Math.round(covered * 100) / 100,
    sidebarCloseControlVisible: visible(collapse),
    sidebarExpandControlVisible: visible(document.querySelector('[data-testid="stExpandSidebarButton"]')),
  };
}
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_app(app_python: Path, port: int, extra_env: dict[str, str]) -> subprocess.Popen:
    env = {
        **os.environ,
        # load_dotenv never overrides a variable that is already set, so these win
        # over .env: no real key, no MiMo, no persistence writes, no demo-cap stop.
        "GEMINI_API_KEY": "offline-stub-not-a-key",
        "MIMO_API_KEY": "",
        "ADS_TTS_PROVIDER": "browser",
        "ADS_PERSISTENCE_ENABLED": "0",
        "ADS_DEMO_DAILY_CAP": "1000",
        **extra_env,
    }
    cmd = [
        str(app_python), "-m", "streamlit", "run", str(STUB_APP),
        "--server.headless", "true", "--server.port", str(port), "--server.fileWatcherType", "none",
    ]
    log = open(tempfile.gettempdir() + f"/screenshot_sweep_{port}.log", "w", encoding="utf-8")  # noqa: SIM115
    return subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def _wait_healthy(proc: subprocess.Popen, port: int, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"app exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("app did not become healthy in time")


def _settle(page: Page) -> None:
    """Wait until Streamlit reports no script run in flight, then a beat for layout."""
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000
    )
    page.wait_for_timeout(800)


def _capture(page: Page, out_dir: Path, stage: str, width: int) -> dict:
    page.set_viewport_size({"width": width, "height": MEASURE_HEIGHT})
    _settle(page)
    metrics = page.evaluate(_METRICS_JS)
    # Streamlit scrolls inside its own container, so a full-page screenshot clips at the
    # viewport; grow the viewport to the content height for the image instead.
    content_height = page.evaluate(
        "() => (document.querySelector('[data-testid=\"stMain\"]') || document.body).scrollHeight"
    )
    page.set_viewport_size({"width": width, "height": min(max(content_height, MEASURE_HEIGHT), 6000)})
    page.wait_for_timeout(500)
    page.screenshot(path=str(out_dir / f"{stage}_{width}.png"))
    page.set_viewport_size({"width": width, "height": MEASURE_HEIGHT})
    return {"stage": stage, "width": width, **metrics}


def _click_if_present(page: Page, name: str) -> bool:
    button = page.get_by_role("button", name=name)
    if button.count() == 0:
        return False
    button.first.click()
    return True


def _sweep_width(page_factory, out_dir: Path, url: str, width: int, stages: set[str]) -> list[dict]:
    results = []

    if "intake" in stages:
        page = page_factory(width)
        page.goto(url)
        page.get_by_text("Academic Defense Simulator").first.wait_for(timeout=60000)
        results.append(_capture(page, out_dir, "intake", width))
        page.close()

    if "example" in stages:
        page = page_factory(width)
        page.goto(url)
        page.get_by_text("Academic Defense Simulator").first.wait_for(timeout=60000)
        _settle(page)
        _click_if_present(page, "Start demo session")  # pre-v1.3a-Phase-C gate only
        page.get_by_role("button", name=EXAMPLE_BUTTON).first.click(timeout=60000)
        page.get_by_text("Session report").first.wait_for(timeout=60000)
        results.append(_capture(page, out_dir, "example", width))
        page.close()

    if stages & {"panel", "running"}:
        page = page_factory(width)
        page.goto(url)
        page.get_by_text("Academic Defense Simulator").first.wait_for(timeout=60000)
        _settle(page)
        _click_if_present(page, "Start demo session")  # pre-v1.3a-Phase-C gate only
        page.get_by_role("button", name="Try a sample capstone").click(timeout=60000)
        page.get_by_role("button", name="Convene the Panel").wait_for(timeout=120000)
        if "panel" in stages:
            results.append(_capture(page, out_dir, "panel", width))
        if "running" in stages:
            page.get_by_role("button", name="Convene the Panel").click()
            page.get_by_label("Your answer").wait_for(timeout=120000)
            results.append(_capture(page, out_dir, "running", width))
        page.close()

    return results


def _passes(result: dict) -> bool:
    inner_ok = result["innerScrollWidth"] is None or result["innerScrollWidth"] <= result["innerClientWidth"]
    return result["scrollWidth"] == result["innerWidth"] and inner_ok


def _print_table(results: list[dict]) -> None:
    print(f"{'stage':<8} {'width':>5}  {'scrollW':>7} {'innerW':>6} {'stMain':>11} {'ok':<3} "
          f"{'main.left':>9} {'main.width':>10}  {'sidebar':<16} {'covers':>6} {'close':<5} {'expand':<6}")
    for r in results:
        main = r["main"] or {}
        side = r["sidebar"]
        side_text = f"{side['left']}..{side['right']}" if side else "hidden"
        ok = "yes" if _passes(r) else "NO"
        inner = f"{r['innerScrollWidth']}/{r['innerClientWidth']}"
        print(f"{r['stage']:<8} {r['width']:>5}  {r['scrollWidth']:>7} {r['innerWidth']:>6} {inner:>11} {ok:<3} "
              f"{main.get('left', '-'):>9} {main.get('width', '-'):>10}  {side_text:<16} "
              f"{r['sidebarCoversMain']:>6} {str(r['sidebarCloseControlVisible']):<5} "
              f"{str(r['sidebarExpandControlVisible']):<6}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for PNGs and metrics.json")
    parser.add_argument("--widths", type=int, nargs="+", default=list(DEFAULT_WIDTHS))
    parser.add_argument("--stages", nargs="+", default=["intake", "panel", "example", "running"],
                        choices=["intake", "panel", "example", "running"])
    parser.add_argument("--app-python", type=Path, default=REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    parser.add_argument("--channel", default="chrome", help="Playwright Chromium channel")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                        help="extra env var for the app server (repeatable)")
    args = parser.parse_args()

    app_python = args.app_python if args.app_python.exists() else Path(sys.executable)
    extra_env = dict(item.split("=", 1) for item in args.env)
    args.out.mkdir(parents=True, exist_ok=True)

    counter_backup = COUNTER_FILE.read_bytes() if COUNTER_FILE.exists() else None
    port = _free_port()
    proc = _start_app(app_python, port, extra_env)
    try:
        _wait_healthy(proc, port)
        url = f"http://127.0.0.1:{port}"
        results: list[dict] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=args.channel, headless=True)
            for width in args.widths:
                def page_factory(w: int):
                    return browser.new_context(viewport={"width": w, "height": MEASURE_HEIGHT}).new_page()
                results.extend(_sweep_width(page_factory, args.out, url, width, set(args.stages)))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if counter_backup is None:
            COUNTER_FILE.unlink(missing_ok=True)
        else:
            COUNTER_FILE.write_bytes(counter_backup)

    (args.out / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    _print_table(results)
    restored = COUNTER_FILE.read_text(encoding="utf-8") if COUNTER_FILE.exists() else "(absent)"
    print(f"\ndemo counter restored: {restored}")
    failures = [r for r in results if not _passes(r)]
    print(f"no horizontal overflow (document and stMain): {'all pass' if not failures else f'{len(failures)} FAIL'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
