"""Panelist voices cloned from a reference clip (v2.0 M8 K5), behind ADS_TTS_VOICE_MODE.

VoiceDesign (v1's `synthesize_speech`) invents a voice from its text description on
every call, so two sentences synthesized separately can sound like two people (M6
H10). `mimo-v2.5-tts-voiceclone` copies the voice in a reference recording instead.
Each archetype's reference is a WAV in backend/voice_refs/, made once with that
archetype's VoiceDesign voice (scripts/measure_voice_clone.py refs), so a cloned
panelist sounds like the designed one, and every part of a split question sounds
like the same person.

v1's MiMo client can't be changed (academic_defense_simulator/ is off limits in v2),
so this is the second module that calls MiMo. It keeps v1's request shape, timeout
and errors: the text to speak is the assistant message, the response's audio comes
back base64 in choices[0].message.audio.data, and every failure is a MimoTTSError,
which backend/tts.py turns into a 502 like any other.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import httpx

from academic_defense_simulator.mimo_provider import API_URL, MimoTTSError, synthesize_speech
from academic_defense_simulator.panel import MIMO_VOICE_DESCRIPTIONS
from backend.tts import Synthesize

MODEL_ID = "mimo-v2.5-tts-voiceclone"
VOICE_REFS = Path(__file__).resolve().parent / "voice_refs"
# v1's VoiceDesign timeout (mimo_provider._TIMEOUT_SECONDS), so the two modes fail alike.
TIMEOUT_SECONDS = 12.0

VoiceMode = Literal["design", "clone"]


def load_voice_refs(directory: Path = VOICE_REFS) -> dict[str, str]:
    """archetype key -> the reference clip as a data URI, for every archetype with a
    voice. Raises FileNotFoundError naming the missing clips, so clone mode can't
    start half-configured."""
    missing = [key for key in MIMO_VOICE_DESCRIPTIONS if not (directory / f"{key}.wav").is_file()]
    if missing:
        raise FileNotFoundError(f"no voice reference for {missing} in {directory}")
    return {
        key: "data:audio/wav;base64," + base64.b64encode((directory / f"{key}.wav").read_bytes()).decode("ascii")
        for key in MIMO_VOICE_DESCRIPTIONS
    }


def clone_payload(text: str, reference: str) -> dict[str, object]:
    """The voiceclone request: no style instruction, so the voice is the clip's alone."""
    return {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": ""}, {"role": "assistant", "content": text}],
        "audio": {"format": "wav", "voice": reference},
    }


def clone_synthesize(refs: Mapping[str, str]) -> Synthesize:
    """A Synthesize (v1's signature) that speaks in the archetype's reference voice."""

    def synthesize(text: str, archetype_key: str, api_key: str) -> bytes:
        try:
            response = httpx.post(
                API_URL,
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json=clone_payload(text, refs[archetype_key]),
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return base64.b64decode(response.json()["choices"][0]["message"]["audio"]["data"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise MimoTTSError(f"Mimo clone synthesis failed for archetype={archetype_key!r}: {exc!r}") from exc

    return synthesize


def synthesize_for(mode: VoiceMode) -> Synthesize:
    """`design` is v1's synthesize_speech itself, so today's requests are unchanged."""
    if mode == "clone":
        return clone_synthesize(load_voice_refs())
    return synthesize_speech
