"""v2.0 M6 H8 and H10: MiMo latency on real questions, whole and split, and the
clips for the by-ear voice check.

Usage (repo root, backend/.venv; needs MIMO_API_KEY, which backend.settings reads
from the environment or backend/.env):

    python scripts/measure_tts_latency.py --max-calls 17

The questions are the M3 live run's, read at run time from the raw api.jsonl in
docs/v2.0-m3-results.md; their text is never printed or written. Each question is
synthesized whole, and its first sentence on its own when backend.tts_split splits
it (a one-part question's first audio is the whole question). H10: the question
that splits is also voiced whole and in parts by three archetypes, saved under
voice-check/split/ (gitignored). Timings go to evals/v2.0-m6-tts-latency.json.
Stops before any call past --max-calls.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.settings import Settings  # noqa: E402
from backend.tts import TtsFailed, default_synthesize, synthesize_wav  # noqa: E402
from backend.tts_split import split_sentences  # noqa: E402

M3_RESULTS = ROOT / "docs" / "v2.0-m3-results.md"
OUTPUT = ROOT / "evals" / "v2.0-m6-tts-latency.json"
CLIPS = ROOT / "voice-check" / "split"
ROUNDS = 2
H10_ARCHETYPES = ["technical_implementation_reviewer", "methodology_expert", "results_conclusions_reviewer"]


@dataclass(frozen=True)
class Question:
    turn: int
    archetype: str
    text: str


def live_questions() -> list[Question]:
    """The questions the M3 live run's /start and /answers returned, in turn order."""
    questions = {}
    for line in M3_RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.startswith('{"at"'):
            continue
        record = json.loads(line)
        if record["status"] != 200 or not record["path"].endswith(("/start", "/answers")):
            continue
        body = json.loads(record["body"])
        question = body.get("question") or body.get("next_question")
        if question:
            questions[question["turn"]] = Question(question["turn"], question["panelist"]["archetype_key"], question["question"])
    return [questions[t] for t in sorted(questions)]


class Budget:
    def __init__(self, max_calls: int, key: str) -> None:
        self.max_calls, self.key, self.made = max_calls, key, 0

    def synthesize(self, text: str, archetype: str) -> tuple[float, bytes | None]:
        """(seconds, WAV bytes or None on failure). Raises SystemExit before going over budget."""
        if self.made >= self.max_calls:
            raise SystemExit(f"STOP: the next call would exceed --max-calls {self.max_calls}")
        self.made += 1
        started = time.perf_counter()
        try:
            audio = synthesize_wav(default_synthesize, text, archetype, self.key)
        except TtsFailed as exc:
            print(f"  call {self.made}: failed after {time.perf_counter() - started:.2f}s ({type(exc).__name__})")
            return time.perf_counter() - started, None
        return time.perf_counter() - started, audio


def concatenate(clips: list[bytes]) -> bytes | None:
    """The parts as one WAV, for listening to them back to back; None if their formats differ."""
    with wave.open(io.BytesIO(clips[0])) as first:
        params = first.getparams()
    out = io.BytesIO()
    with wave.open(out, "wb") as joined:
        joined.setparams(params)
        for clip in clips:
            with wave.open(io.BytesIO(clip)) as part:
                if part.getparams()[:3] != params[:3]:
                    return None
                joined.writeframes(part.readframes(part.getnframes()))
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, required=True)
    args = parser.parse_args()
    key = Settings().mimo_key
    if key is None:
        raise SystemExit("STOP: no MIMO_API_KEY in the environment or backend/.env")
    budget = Budget(args.max_calls, key)
    questions = live_questions()
    print(f"{len(questions)} live questions: " + ", ".join(
        f"turn {q.turn} {q.archetype} {len(q.text)} chars {len(split_sentences(q.text))} part(s)" for q in questions
    ))

    rows = {q.turn: {"turn": q.turn, "archetype": q.archetype, "chars": len(q.text), "words": len(q.text.split()),
                     "parts": len(split_sentences(q.text)), "first_part_chars": len(split_sentences(q.text)[0]),
                     "whole_s": [], "first_part_s": []} for q in questions}
    kept: dict[tuple[int, str], bytes] = {}  # (turn, "whole" | "part0") -> round 1's audio, reused by H10
    for round_number in range(1, ROUNDS + 1):
        for q in questions:
            row = rows[q.turn]
            seconds, audio = budget.synthesize(q.text, q.archetype)
            row["whole_s"].append(round(seconds, 2) if audio else None)
            if audio and round_number == 1:
                kept[(q.turn, "whole")] = audio
            if row["parts"] == 1:
                row["first_part_s"].append(row["whole_s"][-1])  # one part: the first audio is the whole question
            else:
                seconds, audio = budget.synthesize(split_sentences(q.text)[0], q.archetype)
                row["first_part_s"].append(round(seconds, 2) if audio else None)
                if audio and round_number == 1:
                    kept[(q.turn, "part0")] = audio
            print(f"  round {round_number} turn {q.turn}: whole {row['whole_s'][-1]}s, first part {row['first_part_s'][-1]}s")

    whole = [s for r in rows.values() for s in r["whole_s"] if s is not None]
    first = [s for r in rows.values() for s in r["first_part_s"] if s is not None]
    summary = {
        "calls": budget.made,
        "median_whole_s": round(statistics.median(whole), 2),
        "median_time_to_first_audio_s": round(statistics.median(first), 2),
        "h10_latency_rule": "met (<= 8 s)" if statistics.median(first) <= 8 else "not met (> 8 s)",
    }

    # H10: the question that splits, in three voices, whole and in parts.
    split_question = next((q for q in questions if len(split_sentences(q.text)) > 1), None)
    h10 = {}
    if split_question is not None:
        CLIPS.mkdir(parents=True, exist_ok=True)
        parts = split_sentences(split_question.text)
        for archetype in H10_ARCHETYPES:
            own = archetype == split_question.archetype
            whole_audio = kept.get((split_question.turn, "whole")) if own else None
            if whole_audio is None:
                _, whole_audio = budget.synthesize(split_question.text, archetype)
            part_audio = []
            for index, part in enumerate(parts):
                audio = kept.get((split_question.turn, "part0")) if own and index == 0 else None
                if audio is None:
                    _, audio = budget.synthesize(part, archetype)
                part_audio.append(audio)
            files = []
            if whole_audio:
                (CLIPS / f"{archetype}-whole.wav").write_bytes(whole_audio)
                files.append(f"{archetype}-whole.wav")
            if all(part_audio):
                for index, audio in enumerate(part_audio):
                    (CLIPS / f"{archetype}-part{index}.wav").write_bytes(audio)
                    files.append(f"{archetype}-part{index}.wav")
                joined = concatenate(part_audio)
                if joined:
                    (CLIPS / f"{archetype}-split-joined.wav").write_bytes(joined)
                    files.append(f"{archetype}-split-joined.wav")
            h10[archetype] = files
        summary["calls"] = budget.made

    output = {"milestone": "v2.0 M6", "source": "M3 live run questions", "rounds": ROUNDS,
              "questions": list(rows.values()), "summary": summary,
              "h10": {"question_turn": split_question.turn if split_question else None, "clips": h10}}
    OUTPUT.write_text(json.dumps(output, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1))


if __name__ == "__main__":
    main()
