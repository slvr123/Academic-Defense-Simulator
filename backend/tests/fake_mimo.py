"""A stand-in for v1's `synthesize_speech`, for /tts tests. Returns a small WAV by
default; can be told to fail, to return other bytes, or to hold a call open."""

from __future__ import annotations

import threading

from academic_defense_simulator.mimo_provider import MimoTTSError

# A real 44-byte WAV header plus a few samples: starts with RIFF, as MiMo's do.
WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00" + b"\x00\x01" * 8


class FakeMimo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []  # (text, archetype_key, api_key)
        self.audio = WAV
        self.failures: list[Exception] = []  # raised in order, one per call
        self.block: threading.Event | None = None  # wait for this (up to hold_s) before answering
        self.hold_s = 6.0
        self.entered = threading.Event()

    def __call__(self, text: str, archetype_key: str, api_key: str) -> bytes:
        self.calls.append((text, archetype_key, api_key))
        self.entered.set()
        if self.block is not None:
            self.block.wait(self.hold_s)
        if self.failures:
            raise self.failures.pop(0)
        return self.audio


def mimo_error(message: str = "Mimo synthesis failed for archetype='x': ReadTimeout('timed out')") -> MimoTTSError:
    return MimoTTSError(message)
