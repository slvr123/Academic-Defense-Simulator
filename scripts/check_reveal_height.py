"""M10 follow-up acceptance: the paged source reveal never makes the page taller.

    <python with playwright> scripts/check_reveal_height.py

Against `vite preview` of a build made with VITE_API_BASE_URL=http://api.test, with the
canned AniCheck responses of scripts/screens_m10.py. For question 2 (question 1's reveal
showing) it prints the page's height with the slot's content and with it removed
(display: none): at 1280 px the reveal card, and the PDF panel in its place; at 375 px
the reveal bar (its whole slot, gap included). Then the page count: at 1280 the card's,
at 375 the sheet's, opened from the bar. Both for the canned passage and a long one
(the AniCheck methodology pages plus a long answer). Exit status 1 if a 1280 px page
grows at all or a 375 px page grows by more than 56 px.
"""

from __future__ import annotations

import copy
import socket
import subprocess
import sys
import time

from playwright.sync_api import Page, sync_playwright

import screens_m10 as screens

# Long text from the AniCheck sample (pp. 8-9), to force the passage onto several pages.
LONG_PASSAGE = (
    "Leaf images were collected from eleven rice fields within Barangay Masagana over a six-week period spanning the "
    "vegetative and reproductive stages of the 2024 wet season crop. Photographs were captured using a Samsung Galaxy "
    "A54 at a resolution of 3000 by 4000 pixels. To ensure consistency, each leaf was detached and photographed flat "
    "against a white cardboard background under natural daylight between 8:00 AM and 10:00 AM, with the camera held "
    "approximately 25 centimeters from the leaf surface. Labeling was performed by an agricultural technician from the "
    "municipal agriculture office, who identified each photographed leaf on site. Images that the technician could not "
    "confidently classify were discarded. A total of 3,240 images were retained. The tungro class is visibly "
    "underrepresented relative to the others. This reflects the actual incidence encountered during the collection "
    "window rather than a sampling decision. To compensate, data augmentation was applied more aggressively to this "
    "class during training. All images were resized to 224 by 224 pixels to match the input dimensions expected by "
    "MobileNetV2, and pixel values were normalized to the range expected by the pretrained weights. The dataset was "
    "partitioned into a training set and a test set using an 80:20 split, yielding 2,592 training images and 648 test "
    "images."
)
LONG_ANSWER = " ".join([screens.ANSWERS[1]] * 6)


def long_variant() -> tuple[dict, str]:
    """ANSWERED[0] with question 1's passage replaced by the long one, and the long answer."""
    answered = copy.deepcopy(screens.ANSWERED[0])
    start = copy.deepcopy(screens.START)
    reference = "photographed flat against a white cardboard background"
    at = LONG_PASSAGE.index(reference)
    source = start["question"]["sources"][0]
    source.update(text=LONG_PASSAGE, page_start=8, page_end=9, highlight={"start": at, "end": at + len(reference)})
    start["question"]["grounding_reference"] = reference
    return {"start": start, "answered": answered}, LONG_ANSWER


def to_reveal(page: Page, api: screens.Api, start: dict, answered: dict, answer: str) -> None:
    api.set(f"POST /sessions/{screens.SID}/start", start)
    screens.to_question(page, api)
    api.set(f"POST /sessions/{screens.SID}/answers", answered)
    page.get_by_label("Your answer").fill(answer)
    page.get_by_role("button", name="Submit answer").click()
    page.locator("[data-reveal], [data-reveal-bar]").first.wait_for()
    page.wait_for_timeout(300)


def heights(page: Page, element: str) -> tuple[int, int]:
    """The page's height with `element` (a JS expression), then with it hidden; restored after."""
    with_it = page.evaluate("document.documentElement.scrollHeight")
    page.evaluate(f"({element}).style.display = 'none'")
    without = page.evaluate("document.documentElement.scrollHeight")
    page.evaluate(f"({element}).style.display = ''")
    return with_it, without


def main() -> int:
    port = screens.free_port()
    preview = subprocess.Popen(
        f"npx vite preview --port {port} --strictPort", cwd=screens.FRONTEND, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    grew = 0
    try:
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        long, long_answer = long_variant()
        cases = {
            "canned": ({"start": screens.START, "answered": screens.ANSWERED[0]}, screens.ANSWERS[0]),
            "long": (long, long_answer),
        }
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            for text, (data, answer) in cases.items():
                for width, pdf in ((1280, False), (1280, True), (375, False)):
                    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                    page = context.new_page()
                    api = screens.base_api()
                    page.route(f"{screens.API}/**", api.handle)
                    page.goto(f"http://localhost:{port}/")
                    page.get_by_text("Drop your PDF here").wait_for()
                    to_reveal(page, api, data["start"], data["answered"], answer)
                    narrow = width < 1024
                    if pdf:
                        page.get_by_role("button", name="Open at page").click()
                        page.locator('section[aria-label="Your PDF"]').wait_for()
                        element = "document.querySelector('section[aria-label=\"Your PDF\"]')"
                    elif narrow:
                        element = "document.querySelector('[data-reveal-bar]').parentElement.parentElement"  # the slot cell
                    else:
                        element = "document.querySelector('[data-reveal]')"
                    with_it, without = heights(page, element)
                    if narrow:
                        page.locator("[data-reveal-bar]").click()
                        page.get_by_role("dialog", name="Source passage").wait_for()
                        page.wait_for_timeout(200)
                    card = page.evaluate("Math.round(document.querySelector('[data-reveal]').getBoundingClientRect().height)")
                    counter = page.locator("[data-reveal]").get_by_text("/ ").inner_text()
                    scroll = page.evaluate(
                        "Array.from(document.querySelectorAll('[data-reveal] *')).filter(e => e.scrollHeight > e.clientHeight + 1"
                        " && ['auto','scroll'].includes(getComputedStyle(e).overflowY)).length"
                    )
                    what = "PDF open" if pdf else ("bar     " if narrow else "reveal  ")
                    label = f"{text:6} {width}px {what}"
                    grown = with_it - without
                    verdict = "same" if grown <= 0 else f"+{grown}px"
                    if (width >= 1024 and grown > 0) or (narrow and grown > 56):
                        grew += 1
                    shown = "card hidden under the PDF," if pdf else (f"sheet {card}px," if narrow else f"card {card}px,")
                    print(f"{label}  page {with_it} with, {without} without ({verdict})  {shown} pages {counter}, scrolling boxes {scroll}")
                    context.close()
            browser.close()
    finally:
        subprocess.run(f"taskkill /F /T /PID {preview.pid}", shell=True, capture_output=True)
    print(f"{grew} case(s) over the limit (1280: any growth; 375: more than 56 px)")
    return 1 if grew else 0


if __name__ == "__main__":
    sys.exit(main())
