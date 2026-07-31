"""v1.2.2 Task 6 probe: does `st.audio(..., autoplay=True)` actually play without
a prior user gesture, and does an earlier genuine click on the page (the shape of
v1.2.2 Decision 3's disclosure-gate toggle) carry forward to a LATER `st.audio`
element rendered after a Streamlit rerun?

This is a browser-policy question, not a hosting-environment one — autoplay
blocking is enforced by the browser against the page's origin, and Streamlit's
rerun model re-renders the DOM in place without a real navigation, so this is
testable locally with a real browser and the answer transfers to the deployed
path unchanged, unlike latency (v1.2.2 Decision 4).

Renders two `st.audio` elements, both from the same short, real, silent WAV:
  - "cold" -- rendered immediately on first load, no click has happened yet.
  - "after-click" -- rendered only after the button below is clicked (a stand-in
    for v1.2.2 Decision 3's disclosure toggle) and the script reruns.

`st.audio`'s own player has a native play/pause button, so any autoplay failure
is still recoverable by the visitor -- this probe is checking whether that
fallback is even necessary, not whether playback is possible at all.

Usage:
    streamlit run scripts/probe_mimo_autoplay.py
"""

from __future__ import annotations

import io
import wave

import streamlit as st


def _silent_wav(seconds: float = 1.0, sample_rate: int = 8000) -> bytes:
    """A real, valid, silent WAV built via the stdlib `wave` module rather than
    a hand-typed byte literal — this probe has zero dependency on the Mimo API
    or a key either way, since it's testing browser autoplay policy, not
    synthesis, but the header still needs to be genuinely well-formed for the
    `<audio>` element's play() attempt to be a fair test."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * int(sample_rate * seconds))
    return buffer.getvalue()


# st.audio() (Streamlit 1.58.0) takes no `key` argument, so the two players
# are given distinguishably different durations instead — same silence,
# different byte content, different auto-generated element ID.
COLD_WAV = _silent_wav(seconds=1.0)
AFTER_CLICK_WAV = _silent_wav(seconds=1.2)

st.title("Mimo autoplay probe (v1.2.2 Task 6)")

st.write(
    "Cold player below rendered on load, before any click on this page. "
    "Check its `paused` state via the JS console or read_page, then click "
    "the button to render the second player and re-check."
)

st.subheader("1. Cold (no prior click)")
st.audio(COLD_WAV, format="audio/wav", autoplay=True)

if "clicked" not in st.session_state:
    st.session_state.clicked = False

if st.button("Simulate the disclosure-gate click"):
    st.session_state.clicked = True

if st.session_state.clicked:
    st.subheader("2. After a genuine click + rerun")
    st.audio(AFTER_CLICK_WAV, format="audio/wav", autoplay=True)
else:
    st.caption("(second player not rendered yet — click the button above)")
