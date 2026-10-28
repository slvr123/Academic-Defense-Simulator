"""v2.0 M10 N12: render the share image and the favicon PNGs.

    <python with playwright> scripts/render_identity.py

Writes frontend/public/og-image.png (1200x630, from frontend/og/og-image.html),
frontend/public/favicon-32.png and frontend/public/apple-touch-icon.png (from
frontend/public/favicon.svg). Dev-only, like scripts/screenshot_sweep.py: Playwright
in whichever Python runs this, and system Chrome (channel="chrome").
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
PUBLIC = FRONTEND / "public"


def main() -> None:
    svg = (PUBLIC / "favicon.svg").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        page.goto((FRONTEND / "og" / "og-image.html").as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(PUBLIC / "og-image.png"))
        for size, name in ((32, "favicon-32.png"), (180, "apple-touch-icon.png")):
            icon = browser.new_page(viewport={"width": size, "height": size})
            icon.set_content(f"<style>html,body{{margin:0}}svg{{display:block;width:{size}px;height:{size}px}}</style>{svg}")
            icon.screenshot(path=str(PUBLIC / name), omit_background=True)
        browser.close()
    for name in ("og-image.png", "favicon-32.png", "apple-touch-icon.png"):
        print(name, (PUBLIC / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
