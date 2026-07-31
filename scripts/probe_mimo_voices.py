"""v1.2.1 Decision 0b/0c probe: Mimo v2.5 TTS voice-design distinctness and latency.

Standalone. No Streamlit import, no app imports beyond the archetype key list
(`academic_defense_simulator.panel.VOICE_PROFILES`, read only for `.keys()`).

Two things measured, per Decision 0:
  0b — nine archetype voices, described in natural language and synthesized via
       mimo-v2.5-tts-voicedesign, written to evidence/v1.2.1-voices/ for a human
       listening pass. Distinguishability is a listening judgement (Decision 0a's
       precedent) — this script does not render a verdict.
  0c — ten synthesis runs of one realistic question, min/median/max latency.

Model id (Decision 9 — the brief deliberately left it unnamed): confirmed against
Xiaomi's live docs at
https://mimo.mi.com/docs/en-US/quick-start/usage-guide/audio/speech-synthesis-v2.5
on 2026-07-31. See MODEL_ID below.

Two of the brief's Decision 9 integration facts did not match those live docs and
are corrected here (Part 1 is Sean's to edit, not Code's):
  - Style control is NOT `<style>...</style>` XML tags. It is a bracket prefix on
    the content itself: `(Style)Content`, full-width `（Style）Content`, or
    `[Style]Content`. Not used by this probe — the voice descriptions carry tone
    directly, and a distinctness/latency measurement has no need for a
    per-utterance style override.
  - Everything else Decision 9 named checked out against the live docs: the
    OpenAI-chat-completions-shaped endpoint, the `api-key` header (not Bearer),
    target text in the assistant-role message, voice description in the
    user-role message, base64 audio in `message.audio.data`.
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic_defense_simulator.panel import VOICE_PROFILES  # noqa: E402 - archetype key list only

# Confirmed against https://mimo.mi.com/docs/en-US/quick-start/usage-guide/audio/speech-synthesis-v2.5
# on 2026-07-31. Per Decision 9: when this id 404s or the docs rename the series,
# that is Branch D (halt), not a scramble to whatever replaces it.
MODEL_ID = "mimo-v2.5-tts-voicedesign"
MODEL_ID_READ_DATE = "2026-07-31"

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"

VOICES_DIR = Path(__file__).resolve().parent.parent / "evidence" / "v1.2.1-voices"

# ~40 words, defense-flavoured, identical across all nine so any difference heard
# is the voice and not the words (same discipline as probe_voice_resolution.py's
# CONTROLLED_LINE).
CONTROLLED_SENTENCE = (
    "Before we continue, I want to press you on one specific choice in your "
    "methodology, because the panel needs to understand whether that decision "
    "was justified by your data or simply convenient given your timeline and "
    "resources."
)

# ~45 words, one realistic defense question, held constant across all ten latency
# runs (Decision 0c).
LATENCY_QUESTION = (
    "Your document claims a fifteen percent improvement in accuracy over the "
    "baseline model, but the sample size in your evaluation set is quite small. "
    "Can you walk the panel through why you believe this result would "
    "generalize to a larger, more diverse population of users?"
)

LATENCY_RUNS = 10

# Decision 5: descriptions authored to the existing archetype character (see
# ARCHETYPE_CONFIG in prompts/panelist_prompts.py) and current avatar gender
# assignments (see the gender comments on VOICE_PROFILES in panel.py — female:
# methodology_expert, literature_theory_specialist, problem_objectives_reviewer,
# statistical_analysis_reviewer; male: the remaining five). This is a probe-local
# draft for the 0b measurement, not the production mapping — Task 2.2 owns
# MIMO_VOICE_DESCRIPTIONS in panel.py, and only on Branch A or B.
VOICE_DESCRIPTIONS: dict[str, str] = {
    "methodology_expert": (
        "A composed woman in her 40s, precise academic diction, a calm and "
        "measured pace, a low warm timbre. She speaks like a rigorous "
        "methodologist double-checking your assumptions."
    ),
    "literature_theory_specialist": (
        "A thoughtful woman in her 30s, articulate and unhurried, a faintly "
        "British cadence, a warm mellow voice. She speaks like a well-read "
        "theorist weighing every citation before she trusts it."
    ),
    "technical_implementation_reviewer": (
        "An energetic man in his 30s, clipped and direct, a brisk pace, a "
        "clear crisp timbre. He speaks like a hands-on engineer who wants to "
        "see exactly how it was built."
    ),
    "ethics_practicality_reviewer": (
        "A grave older man, a deep resonant voice, an unhurried deliberate "
        "pace, a serious sober tone. He speaks like a pragmatic reviewer "
        "weighing real-world consequences."
    ),
    "devils_advocate": (
        "A sharp confident man in his 40s, fast and challenging, an edged "
        "assertive tone. He speaks like a devil's advocate hunting for the "
        "weakest claim in the room."
    ),
    "problem_objectives_reviewer": (
        "A clear-voiced woman in her 30s, a neutral even tone, a brisk "
        "efficient pace. She speaks like a reviewer who wants the research "
        "question stated precisely and nothing more."
    ),
    "statistical_analysis_reviewer": (
        "An analytical woman in her late 30s, crisp precise diction, an even "
        "measured pace, a faintly clinical tone. She speaks like a "
        "statistician checking every number against its claim."
    ),
    "results_conclusions_reviewer": (
        "A bright alert man in his 30s, a slightly higher pitch, an "
        "evaluative inquisitive tone, a moderate brisk pace. He speaks like a "
        "scholar testing whether the conclusions overreach."
    ),
    "industry_practice_reviewer": (
        "A grounded practical man in his 50s, a low steady voice, an "
        "unhurried confident pace. He speaks like an industry veteran asking "
        "whether this would survive contact with a real deployment."
    ),
}


def _check_archetype_coverage() -> None:
    expected = set(VOICE_PROFILES)
    got = set(VOICE_DESCRIPTIONS)
    if expected != got:
        raise SystemExit(
            "VOICE_DESCRIPTIONS does not cover VOICE_PROFILES's archetype set.\n"
            f"missing: {expected - got}\nextra: {got - expected}"
        )


def _api_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    key = os.getenv("MIMO_API_KEY", "").strip()
    if not key:
        raise SystemExit("MIMO_API_KEY is not set (checked environment and .env).")
    return key


def synthesize(client: httpx.Client, api_key: str, *, voice_description: str, text: str) -> bytes:
    """One call to mimo-v2.5-tts-voicedesign. Returns raw wav bytes.

    Decision 9, confirmed 2026-07-31: voice description is the USER message,
    synthesis target text is the ASSISTANT message — reversed from the
    chat-completions norm, and the exact thing the brief flagged as non-obvious
    enough to cost someone a debugging session.
    """
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "user", "content": voice_description},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav"},
    }
    response = client.post(
        API_URL,
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    audio_b64 = data["choices"][0]["message"]["audio"]["data"]
    return base64.b64decode(audio_b64)


def run_distinctness(client: httpx.Client, api_key: str) -> dict[str, str]:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for archetype, description in VOICE_DESCRIPTIONS.items():
        print(f"synthesizing {archetype}...", flush=True)
        audio = synthesize(client, api_key, voice_description=description, text=CONTROLLED_SENTENCE)
        out_path = VOICES_DIR / f"{archetype}.wav"
        out_path.write_bytes(audio)
        results[archetype] = str(out_path)
        print(f"  wrote {out_path} ({len(audio)} bytes)", flush=True)
    return results


def run_latency(client: httpx.Client, api_key: str) -> list[float]:
    # One fixed description (methodology_expert) for all ten runs — Decision 0c
    # holds the target text constant; latency should track text length and
    # network conditions, not which description was sent.
    description = VOICE_DESCRIPTIONS["methodology_expert"]
    durations: list[float] = []
    for i in range(1, LATENCY_RUNS + 1):
        start = time.perf_counter()
        synthesize(client, api_key, voice_description=description, text=LATENCY_QUESTION)
        elapsed = time.perf_counter() - start
        durations.append(elapsed)
        print(f"  run {i}/{LATENCY_RUNS}: {elapsed:.3f}s", flush=True)
    return durations


def main() -> None:
    _check_archetype_coverage()
    api_key = _api_key()

    with httpx.Client() as client:
        print("=== Decision 0b: voice-design distinctness ===", flush=True)
        print(f"model: {MODEL_ID} (read {MODEL_ID_READ_DATE})", flush=True)
        files = run_distinctness(client, api_key)

        print("\n=== Decision 0c: latency (10 runs) ===", flush=True)
        durations = run_latency(client, api_key)

    stats = {
        "model_id": MODEL_ID,
        "model_id_read_date": MODEL_ID_READ_DATE,
        "runs_seconds": durations,
        "min_seconds": min(durations),
        "median_seconds": statistics.median(durations),
        "max_seconds": max(durations),
    }
    print("\nlatency stats:", json.dumps(stats, indent=2))
    print("\nvoice files:", json.dumps(files, indent=2))


if __name__ == "__main__":
    main()
