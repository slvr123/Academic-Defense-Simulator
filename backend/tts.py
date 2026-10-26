"""Panelist voices for POST /tts: v1's MiMo client, with v2's check on what comes back.

v1's `synthesize_speech` is used unchanged (M3b E4: no v1 fixes). It base64-decodes
whatever MiMo returns, and `""` decodes to `b""` without raising, which is the
suspected cause of v1's empty `0:00 / 0:00` player on 2026-10-21. So v2 checks the
bytes itself: empty, or not a RIFF (WAV) file, is a failure like any other.
"""

from __future__ import annotations

from collections.abc import Callable

from academic_defense_simulator.mimo_provider import MimoTTSError, synthesize_speech

# (text, archetype_key, api_key) -> WAV bytes. v1's signature; tests pass a fake.
Synthesize = Callable[[str, str, str], bytes]

WAV_MAGIC = b"RIFF"


class TtsFailed(Exception):
    """MiMo failed, timed out, or sent something that isn't audio."""


def synthesize_wav(synthesize: Synthesize, text: str, archetype_key: str, api_key: str) -> bytes:
    try:
        audio = synthesize(text, archetype_key, api_key)
    except MimoTTSError as exc:
        raise TtsFailed(str(exc)) from exc
    if not audio:
        raise TtsFailed(f"MiMo returned empty audio for archetype={archetype_key!r}")
    if not audio.startswith(WAV_MAGIC):
        raise TtsFailed(f"MiMo returned {len(audio)} bytes that aren't a WAV file (first 4: {audio[:4]!r})")
    return audio


default_synthesize: Synthesize = synthesize_speech
