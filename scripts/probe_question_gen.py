"""Question-generation probe (v0.2.5 hardening, Task 4).

Formal re-verification that flash-lite question generation holds to the Day 3 bar, on
BOTH prompt paths — new-topic (PANELIST_SYSTEM_PROMPT) and follow-up
(FOLLOWUP_SYSTEM_PROMPT). Question generation was only sanity-checked when scoring moved
to flash-lite; this is the evidence.

Reuses the real orchestration helpers in `main.py` (retrieval, prompt rendering, scoring,
branching) so what runs here is what runs in a session — not a re-implementation. Per
question it records raw model output to `scripts/probe_question_gen_results.jsonl`:
  (a) grounded  — automated, via grounding.is_grounded() against the turn's chunk
  (b) in_lane / (c) difficulty_ok — human-judged, left null for Sean to fill in

Follow-up path grounding is expected to run lower than new-topic: the v0.2 eval
(`docs/v0.2-eval-results.md` Finding C) found follow-up `grounding_reference` often echoes
the candidate's own prior answer rather than the source document. That is the finding to
confirm here, not a bug in the check — the 0.85 threshold and is_grounded() are NOT tuned
on the strength of this run.

Run: python scripts/probe_question_gen.py
Budget: ~10 flash-lite calls (4 new-topic generate + 2×(generate + score + follow-up)).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import academic_defense_simulator.main as main
from academic_defense_simulator.config import load_settings
from academic_defense_simulator.grounding import grounding_ratio, is_grounded
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PROMPT_VERSION
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

_ARCHETYPE_KEY = "methodology_expert"  # same archetype as all prior evidence — no new variables
_PDF_PATH = Path(__file__).resolve().parent.parent / "Group2_Library Management System for DAZSMA Documentation (1).pdf"
_RESULTS_PATH = Path(__file__).resolve().parent / "probe_question_gen_results.jsonl"
_DEFENSE_TYPE = DefenseType.CAPSTONE

# Vague, numberless, non-committal — the Day 5 weak-answer pattern. Generic enough to
# apply to whatever methodology question is asked, and it should fail the "strong" gate.
_WEAK_ANSWER = (
    "We felt it was a reasonable choice given our timeline, and the results seemed to line "
    "up with what we expected, so we think it holds up fine for a project of this scope."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile() -> DefenseProfile:
    return DefenseProfile(
        defense_type=_DEFENSE_TYPE,
        domain="library and information science",
        topic="Library Management System for DAZSMA",
        document_id="dazsma-probe",
    )


def _record_for(turn: ConversationTurn, path: str, requested_difficulty: int, **extra) -> dict:
    """Uniform grounding check: a turn carries the chunk it was grounded in (chunk_text),
    which for a follow-up is the re-injected parent chunk — so this is correct on both paths."""
    grounded = is_grounded(turn.grounding_reference, turn.chunk_text)
    ratio = grounding_ratio(turn.grounding_reference, turn.chunk_text)
    record = {
        "record_type": "question",
        "timestamp": _now_iso(),
        "path": path,
        "chunk_index": turn.chunk_index,
        "requested_difficulty": requested_difficulty,
        "returned_difficulty_level": turn.difficulty_level,
        "question": turn.question,
        "grounding_reference": turn.grounding_reference,
        "grounded": grounded,
        "grounding_ratio": round(ratio, 4),
        "in_lane": None,  # human-judged (b)
        "difficulty_ok": None,  # human-judged (c)
    }
    record.update(extra)
    return record


def main_probe() -> None:
    settings = load_settings()
    model = settings.gemini_model
    print(f"[model: {model}] [prompt_version: {PROMPT_VERSION}]")
    print(f"[pdf: {_PDF_PATH.name}]")

    texts = chunk_pdf(str(_PDF_PATH))
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    print(f"[chunks: {len(chunks)}]")

    provider = GeminiProvider(api_key=settings.gemini_api_key, model=model)
    archetype = ARCHETYPE_CONFIG[_ARCHETYPE_KEY]
    pacing = main.MODEL_CALL_DELAY_SECONDS.get(model, main.DEFAULT_CALL_DELAY)

    records: list[dict] = []

    # --- New-topic path: 4 questions, forced across distinct chunks, difficulties 2/4. ---
    # A session with only scoreless turns keeps _should_follow_up False, so every call takes
    # the new-topic branch; appending each turn adds its chunk to used_chunk_indices, so the
    # next retrieve() excludes it — the real loop's variety mechanism.
    print("\n=== New-topic path ===")
    nt_session = DefenseSession(profile=_profile(), difficulty_current=2)
    for requested in (2, 4, 2, 4):
        nt_session.difficulty_current = requested
        turn = main._generate_question(
            provider, nt_session, chunks, embedding_model, archetype, "", model
        )
        rec = _record_for(turn, "new_topic", requested)
        records.append(rec)
        print(f"  chunk {turn.chunk_index} @diff{requested}: grounded={rec['grounded']} "
              f"ratio={rec['grounding_ratio']} — {turn.question[:70]}...")
        nt_session.turns.append(turn)  # score stays None -> next call is new-topic, excludes this chunk

    # --- Follow-up path: 2 rounds, each new-topic -> weak answer -> score -> follow-up. ---
    print("\n=== Follow-up path ===")
    excluded_parent_chunks: list[int] = []
    for round_num in range(1, 3):
        fu_session = DefenseSession(profile=_profile(), difficulty_current=3)
        # Seed scoreless turns on already-used parent chunks so this round's new-topic picks
        # a different chunk (nicer variety; not strictly required by the brief).
        for idx in excluded_parent_chunks:
            fu_session.turns.append(
                ConversationTurn(question="seed", grounding_reference="seed", chunk_index=idx,
                                 chunk_text=chunks[idx].text, difficulty_level=3)
            )

        parent = main._generate_question(provider, fu_session, chunks, embedding_model, archetype, "", model)
        fu_session.turns.append(parent)
        excluded_parent_chunks.append(parent.chunk_index)

        parent.answer = _WEAK_ANSWER
        parent.score = main._score_answer(provider, parent, archetype, _DEFENSE_TYPE.value)
        time.sleep(pacing)  # _score_answer doesn't pace itself; keep RPM safe before the next call
        is_strong = main._is_strong_answer(parent.score)
        fires = main._should_follow_up(fu_session)
        print(f"  round {round_num}: parent chunk {parent.chunk_index}, "
              f"primary_gap={parent.score.primary_gap!r}, is_strong={is_strong}, follow_up_fires={fires}")

        if not fires:
            print(f"  round {round_num}: follow-up did not fire (answer scored strong or no gap) — "
                  f"recording parent only, skipping follow-up generation.")
            records.append(_record_for(parent, "new_topic", 3,
                                       note="intended as follow-up parent; follow-up gate did not fire"))
            continue

        follow_up = main._generate_question(provider, fu_session, chunks, embedding_model, archetype, "", model)
        rec = _record_for(
            follow_up, "follow_up", 3,
            parent_chunk_index=parent.chunk_index,
            parent_question=parent.question,
            scored_answer=_WEAK_ANSWER,
            primary_gap=parent.score.primary_gap,
            parent_is_strong=is_strong,
        )
        records.append(rec)
        print(f"  round {round_num}: follow-up on chunk {follow_up.chunk_index}: "
              f"grounded={rec['grounded']} ratio={rec['grounding_ratio']} — {follow_up.question[:70]}...")

    _write_results(records, model)


def _write_results(records: list[dict], model: str) -> None:
    meta = {
        "record_type": "meta",
        "generated_at": _now_iso(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "archetype": _ARCHETYPE_KEY,
        "grounding_threshold": 0.85,
        "document": _PDF_PATH.name,
        # Task 1 citation: the archetype-lane fix re-verification is already recorded in
        # docs/v0.2-eval-results.md Check 2 (methodology lane held across both eval runs;
        # no repeat of the Day-4 chunk-25 leak) — though neither run reached deep chunk
        # exhaustion, so it was not re-stress-tested at the point it originally failed. The
        # (b) in_lane column below therefore confirms rather than establishes.
        "lane_reverification_note": "See docs/v0.2-eval-results.md Check 2; in_lane column confirms, not establishes.",
        "followup_grounding_note": (
            "Follow-up grounding is expected lower than new-topic (docs/v0.2-eval-results.md "
            "Finding C: follow-up grounding_reference often echoes the candidate's answer, not "
            "the source chunk). Threshold and is_grounded() are NOT tuned on this run."
        ),
    }
    with _RESULTS_PATH.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Pass/fail tally broken out by path.
    print(f"\n=== Grounding tally by path (threshold 0.85) ===")
    for path in ("new_topic", "follow_up"):
        rows = [r for r in records if r["path"] == path]
        passed = sum(1 for r in rows if r["grounded"])
        print(f"  {path}: {passed}/{len(rows)} grounded; "
              f"ratios={[r['grounding_ratio'] for r in rows]}")
    print(f"\nWrote {len(records)} question records (+1 meta) to {_RESULTS_PATH}")


if __name__ == "__main__":
    main_probe()
