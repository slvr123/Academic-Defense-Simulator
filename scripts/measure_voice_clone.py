"""v2.0 M8 K5: voice cloning, measured. Three steps, each its own command.

Usage (repo root, backend/.venv; MIMO_API_KEY via backend.settings):

    python scripts/measure_voice_clone.py free-check --max-calls 40
    python scripts/measure_voice_clone.py refs --text-file voice-check/clone/reference-text.txt --max-calls 40
    python scripts/measure_voice_clone.py measure --max-calls 40

- free-check: one voiceclone call with an existing local VoiceDesign clip as the
  reference; prints the response's status and every field except the audio, so the
  billing fields can be read.
- refs: one VoiceDesign call per archetype (v1's voice map and descriptions, unchanged)
  speaking the reference sentence, resampled to 16 kHz mono and written to
  backend/voice_refs/<archetype>.wav (committed). The sentence is read from a
  gitignored file: no voice-reference text may be committed except in the WAVs.
- measure: on the M3 live run's four questions (as M6 H8), each voiced whole by
  clone in its own panelist's voice; the question that splits also has its first
  part timed alone (time to first audio). For the by-ear check, that question is
  also voiced by 3 archetypes as clone-split-joined, clone-whole and design-whole, in
  voice-check/clone/ (gitignored). Timings go to evals/v2.0-m8-voice-clone.json.

Every MiMo call, across the three commands, is counted in voice-check/clone/ledger.json,
and no call is made that would take the total past --max-calls. Question text is
never printed or written.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import numpy as np  # noqa: E402

from academic_defense_simulator.mimo_provider import API_URL, synthesize_speech  # noqa: E402
from academic_defense_simulator.panel import MIMO_VOICE_DESCRIPTIONS  # noqa: E402
from backend.settings import Settings  # noqa: E402
from backend.tts import Synthesize, TtsFailed, synthesize_wav  # noqa: E402
from backend.tts_clone import VOICE_REFS, clone_payload, clone_synthesize, load_voice_refs  # noqa: E402
from backend.tts_split import split_sentences  # noqa: E402
from scripts.measure_tts_latency import H10_ARCHETYPES, live_questions  # noqa: E402

CLIPS = ROOT / "voice-check" / "clone"
LEDGER = CLIPS / "ledger.json"
OUTPUT = ROOT / "evals" / "v2.0-m8-voice-clone.json"
FREE_CHECK_REFERENCE = ROOT / "voice-check" / "technical_implementation_reviewer.wav"
REF_RATE = 16_000
REF_SECONDS = (6.0, 10.0)
REFS_MAX_BYTES = 3 * 1024 * 1024


class Ledger:
    """MiMo calls across every command; refuses a call past the cap."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.made = json.loads(LEDGER.read_text(encoding="utf-8"))["calls"] if LEDGER.exists() else 0

    def spend(self) -> None:
        if self.made + 1 > self.max_calls:
            raise SystemExit(f"STOP: the next MiMo call would take the total past --max-calls {self.max_calls}")
        self.made += 1
        LEDGER.write_text(json.dumps({"calls": self.made}) + "\n", encoding="utf-8")


def timed(ledger: Ledger, synthesize: Synthesize, text: str, archetype: str, key: str) -> tuple[float, bytes | None]:
    ledger.spend()
    started = time.perf_counter()
    try:
        audio = synthesize_wav(synthesize, text, archetype, key)
    except TtsFailed as exc:
        print(f"  call {ledger.made}: {archetype} failed after {time.perf_counter() - started:.2f}s: {exc}")
        return round(time.perf_counter() - started, 2), None
    return round(time.perf_counter() - started, 2), audio


def seconds(audio: bytes) -> float:
    with wave.open(io.BytesIO(audio)) as clip:
        return clip.getnframes() / clip.getframerate()


def resample(audio: bytes, rate: int = REF_RATE) -> bytes:
    """Mono 16-bit WAV at `rate`: a windowed-sinc low-pass at the new Nyquist, then
    linear interpolation onto the new sample times."""
    with wave.open(io.BytesIO(audio)) as clip:
        if (clip.getnchannels(), clip.getsampwidth()) != (1, 2):
            raise SystemExit(f"STOP: expected mono 16-bit audio, got {clip.getnchannels()} ch x {clip.getsampwidth()} B")
        source_rate = clip.getframerate()
        samples = np.frombuffer(clip.readframes(clip.getnframes()), dtype="<i2").astype(np.float64)
    if source_rate != rate:
        cutoff = 0.5 * rate / source_rate * 0.95
        taps = np.arange(-64, 65)
        kernel = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.hamming(len(taps))
        filtered = np.convolve(samples, kernel / kernel.sum(), mode="same")
        times = np.arange(int(len(samples) * rate / source_rate)) * source_rate / rate
        samples = np.interp(times, np.arange(len(samples)), filtered)
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(np.clip(np.round(samples), -32768, 32767).astype("<i2").tobytes())
    return out.getvalue()


def free_check(ledger: Ledger, key: str, text: str) -> None:
    import base64

    reference = "data:audio/wav;base64," + base64.b64encode(FREE_CHECK_REFERENCE.read_bytes()).decode("ascii")
    ledger.spend()
    started = time.perf_counter()
    response = httpx.post(API_URL, headers={"api-key": key, "Content-Type": "application/json"},
                          json=clone_payload(text, reference), timeout=30.0)
    elapsed = time.perf_counter() - started
    print(f"HTTP {response.status_code} in {elapsed:.2f}s")
    body = response.json()
    audio = body.get("choices", [{}])[0].get("message", {}).get("audio", {})
    if "data" in audio:
        audio["data"] = f"<{len(audio['data'])} base64 chars>"
    for choice in body.get("choices", []):
        choice.get("message", {}).pop("content", None)
    print(json.dumps(body, indent=1))


def make_refs(ledger: Ledger, key: str, text: str, only: list[str]) -> None:
    """`only` remakes just those archetypes' clips (all when empty)."""
    VOICE_REFS.mkdir(exist_ok=True)
    report = {}
    for archetype in only or MIMO_VOICE_DESCRIPTIONS:
        elapsed, audio = timed(ledger, synthesize_speech, text, archetype, key)
        if audio is None:
            raise SystemExit(f"STOP: no reference clip for {archetype}")
        clip = resample(audio)
        length = seconds(clip)
        (VOICE_REFS / f"{archetype}.wav").write_bytes(clip)
        report[archetype] = {"design_s": elapsed, "seconds": round(length, 2), "bytes": len(clip)}
        flag = "" if REF_SECONDS[0] <= length <= REF_SECONDS[1] else "  OUTSIDE 6-10 s"
        print(f"  {archetype:32} {length:5.2f} s  {len(clip):7d} bytes  (design call {elapsed:.2f} s){flag}")
    total = sum(path.stat().st_size for path in VOICE_REFS.glob("*.wav"))
    print(f"total {total} bytes ({total / 1024 / 1024:.2f} MB, cap 3 MB); calls so far {ledger.made}")
    if total > REFS_MAX_BYTES:
        raise SystemExit("STOP: the reference clips are over 3 MB")


def joined(clips: list[bytes]) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        for n, clip in enumerate(clips):
            with wave.open(io.BytesIO(clip)) as source:
                if n == 0:
                    target.setparams(source.getparams())
                target.writeframes(source.readframes(source.getnframes()))
    return out.getvalue()


def measure(ledger: Ledger, key: str) -> None:
    clone = clone_synthesize(load_voice_refs())
    questions = live_questions()
    rows, split_question = [], None
    for question in questions:
        parts = split_sentences(question.text)
        whole_s, whole = timed(ledger, clone, question.text, question.archetype, key)
        row = {"turn": question.turn, "archetype": question.archetype, "chars": len(question.text),
               "parts": len(parts), "clone_whole_s": whole_s, "clone_whole_ok": whole is not None}
        if len(parts) > 1 and split_question is None:
            split_question = question
            part_times, part_audio = [], []
            for part in parts:
                elapsed, audio = timed(ledger, clone, part, question.archetype, key)
                part_times.append(elapsed)
                part_audio.append(audio)
            row["clone_part_s"] = part_times
            row["first_audio_s"] = part_times[0]
            save_clips(question.archetype, whole, part_audio, None)
        else:
            row["first_audio_s"] = whole_s  # one part: the first audio is the whole question
        print(f"  turn {question.turn} {question.archetype:32} chars={row['chars']} parts={row['parts']} "
              f"whole={whole_s}s first_audio={row['first_audio_s']}s")
        rows.append(row)
    if split_question is None:
        raise SystemExit("STOP: none of the questions splits, so there is no split clip to judge")

    for archetype in H10_ARCHETYPES:
        whole_design = timed(ledger, synthesize_speech, split_question.text, archetype, key)[1]
        if archetype == split_question.archetype:
            save_clips(archetype, None, None, whole_design)
            continue
        whole = timed(ledger, clone, split_question.text, archetype, key)[1]
        parts = [timed(ledger, clone, part, archetype, key)[1] for part in split_sentences(split_question.text)]
        save_clips(archetype, whole, parts, whole_design)

    firsts = [r["first_audio_s"] for r in rows]
    summary = {
        "milestone": "v2.0 M8 K5", "source": "M3 live run questions (as M6 H8)", "model": "mimo-v2.5-tts-voiceclone",
        "questions": rows, "median_first_audio_s": round(statistics.median(firsts), 2),
        "median_clone_whole_s": round(statistics.median(r["clone_whole_s"] for r in rows), 2),
        "split_question_turn": split_question.turn, "mimo_calls_across_commands": ledger.made,
    }
    OUTPUT.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "questions"}, indent=1))


def save_clips(archetype: str, whole: bytes | None, parts: list[bytes | None] | None, design: bytes | None) -> None:
    if whole is not None:
        (CLIPS / f"{archetype}-clone-whole.wav").write_bytes(whole)
    if parts is not None and all(p is not None for p in parts):
        (CLIPS / f"{archetype}-clone-split-joined.wav").write_bytes(joined([p for p in parts if p is not None]))
    if design is not None:
        (CLIPS / f"{archetype}-design-whole.wav").write_bytes(design)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["free-check", "refs", "measure"])
    parser.add_argument("--max-calls", type=int, required=True, help="the cap on MiMo calls across every step")
    parser.add_argument("--text-file", type=Path, help="the reference sentence (refs, free-check); gitignored")
    parser.add_argument("--only", nargs="*", default=[], choices=list(MIMO_VOICE_DESCRIPTIONS),
                        help="refs: remake only these archetypes' clips")
    args = parser.parse_args()
    key = Settings().mimo_key
    if key is None:
        raise SystemExit("STOP: no MIMO_API_KEY")
    CLIPS.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(args.max_calls)
    print(f"{ledger.made} MiMo calls made before this command; cap {args.max_calls}")
    steps: dict[str, Callable[[], None]] = {
        "free-check": lambda: free_check(ledger, key, args.text_file.read_text(encoding="utf-8").strip()),
        "refs": lambda: make_refs(ledger, key, args.text_file.read_text(encoding="utf-8").strip(), args.only),
        "measure": lambda: measure(ledger, key),
    }
    steps[args.step]()


if __name__ == "__main__":
    main()
