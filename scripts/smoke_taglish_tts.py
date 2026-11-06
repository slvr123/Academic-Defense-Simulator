"""M16 Decision 6: the one voice smoke test for Taglish. Synthesizes one generated Taglish
question through the backend's own /tts path (the configured voice mode, v2's WAV check)
and reports what came back. Nothing is measured beyond that; whether it sounds right is
for a person with headphones.

    backend/.venv/Scripts/python -u scripts/smoke_taglish_tts.py OUT.wav
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.settings import Settings  # noqa: E402
from backend.tts import synthesize_wav  # noqa: E402
from backend.tts_clone import synthesize_for  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1])
    lines = json.loads((REPO_ROOT / "evals" / "v2.0-m16-taglish-lines.json").read_text(encoding="utf-8"))["decision_5_lines"]
    line = next(item for item in lines if item["kind"] == "closing")  # short, and mostly Filipino
    settings = Settings()
    key = settings.mimo_key
    if key is None:
        raise SystemExit("no MiMo key configured")
    started = time.perf_counter()
    audio = synthesize_wav(synthesize_for(settings.tts_voice_mode), line["line"], line["archetype_key"], key)
    took = round((time.perf_counter() - started) * 1000)
    out.write_bytes(audio)
    with wave.open(str(out)) as clip:
        seconds = clip.getnframes() / clip.getframerate()
    print(f"voice_mode={settings.tts_voice_mode} archetype={line['archetype_key']} text={line['line']!r}")
    print(f"ok: {len(audio)} bytes, RIFF header, {seconds:.2f} s of audio, in {took} ms -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
