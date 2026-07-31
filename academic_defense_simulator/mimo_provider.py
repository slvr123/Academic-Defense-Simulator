"""Mimo TTS provider — the only module that talks to Xiaomi's MiMo API.

v1.2.1 Decision 2 (server-side audio, narrowed to the env-flagged local path) and
Decision 9 (model pinning). Isolated the same way `llm/gemini_provider.py` is the
only module that imports `google.genai`: everything about Mimo's request/response
shape lives here, and the render call site in `streamlit_app.py` sees only
`synthesize_speech` and `MimoTTSError`.
"""

from __future__ import annotations

import base64

import httpx

from academic_defense_simulator.panel import MIMO_VOICE_DESCRIPTIONS

# Confirmed against
# https://mimo.mi.com/docs/en-US/quick-start/usage-guide/audio/speech-synthesis-v2.5
# on 2026-07-31 (v1.2.1 Decision 9 correction; Phase 1 evidence:
# evidence/v1.2.1-mimo-distinctness.txt, evidence/v1.2.1-latency.txt). Decision 0b
# requires voice-design-by-text-description, not the preset catalog — this is the
# voicedesign variant, not the base `mimo-v2.5-tts`. When this id 404s or the docs
# rename the series, that is Branch D of Decision 0: halt and record, not a
# scramble to the next model.
MODEL_ID = "mimo-v2.5-tts-voicedesign"
MODEL_ID_READ_DATE = "2026-07-31"

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"

# v1.2.1 Decision 0c measured min 4.947s / median 6.160s / max 7.395s on a real
# ~45-word question (evidence/v1.2.1-latency.txt), on Sean's own machine and
# connection — the optimistic case, not the typical one. Decision 6: a failed
# synthesis must not extend the pause beyond what the browser path would have
# taken, so this has to be short enough to fail cleanly rather than hang, while
# staying above the slowest observed successful run.
_TIMEOUT_SECONDS = 12.0


class MimoTTSError(Exception):
    """Raised on any Mimo synthesis failure — network, auth, quota, malformed
    response, or timeout. Decision 6: the call site catches this (and nothing more
    specific) and falls back to the browser path for that turn."""


def synthesize_speech(text: str, archetype_key: str, api_key: str) -> bytes:
    """One call to `mimo-v2.5-tts-voicedesign`. Returns raw wav bytes.

    Decision 9, confirmed against live docs 2026-07-31: the voice description is
    the USER message, the text to speak is the ASSISTANT message — reversed from
    the chat-completions norm, and the exact thing the brief flagged as non-obvious
    enough to cost someone a debugging session. `archetype_key` is looked up in
    `MIMO_VOICE_DESCRIPTIONS`; a missing key is a `KeyError`, wrapped below into
    `MimoTTSError` like every other failure mode — Decision 6 does not carve out
    an exception for a programming error over a network one.
    """
    try:
        voice_description = MIMO_VOICE_DESCRIPTIONS[archetype_key]
        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "user", "content": voice_description},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": "wav"},
        }
        response = httpx.post(
            API_URL,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        audio_b64 = data["choices"][0]["message"]["audio"]["data"]
        return base64.b64decode(audio_b64)
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise MimoTTSError(f"Mimo synthesis failed for archetype={archetype_key!r}: {exc!r}") from exc
