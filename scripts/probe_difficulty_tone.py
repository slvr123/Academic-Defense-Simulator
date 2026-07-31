"""Difficulty-tone probe (v0.4c Task 3). See
docs/v0.4c-second-doc-eval-decisions.md Decision 3.

Closes 0.3d live-testing finding #2(b): does `PANELIST_SYSTEM_PROMPT` tonally
differentiate difficulty 1 from difficulty 4, or does it only escalate content while
the register stays flat? Paired generation — same document, same chunk, same
archetype, same persona, same template; the ONLY variable is `difficulty_level`.
New-topic path only (Decision 3: follow-up tone is confounded by the
prior-answer-acknowledgment instruction, a different probe's question).

4 pairs: {methodology_expert, technical_implementation_reviewer} x 2 chunks each,
8 generation calls total, zero scoring calls. For each archetype, chunk-slot 1 is
whatever the archetype's retrieval query naturally top-1s on this document with no
exclusions; chunk-slot 2 is the next-best chunk once slot 1 is excluded (same
seeded-scoreless-turn trick `probe_question_gen.py` uses for follow-up-round
variety). Within one chunk slot, the difficulty-1 and difficulty-4 calls each run
against a session with IDENTICAL used_chunk_indices (neither turn is appended to the
other's session) so retrieval — deterministic cosine similarity, no sampling —
lands on the same chunk both times; this is asserted, not assumed.

Emits three files:
  - probe_difficulty_tone_results.jsonl — full raw record per generated question,
    both difficulties, is_grounded()/grounding_ratio always computed (Decision 3:
    difficulty-4 grounding misses are first-class findings on this document).
  - probe_difficulty_tone_judging.jsonl — pair_id + question A/B only, shuffled,
    difficulty labels stripped. For Sean's blind read: this script does not judge
    anything (blind judgment must come from a human with no foreknowledge of which
    side was requested at difficulty 4).
  - probe_difficulty_tone_key.jsonl — the A/B -> difficulty mapping plus the fixed
    shuffle seed, so unblinding is a mechanical lookup, never a reconstruction.

Run: python scripts/probe_difficulty_tone.py
Budget: 8 flash-lite calls (generation only, no scoring, no follow-up).
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import academic_defense_simulator.engine as engine
from academic_defense_simulator.config import load_settings
from academic_defense_simulator.grounding import grounding_ratio, is_grounded
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.prompts.panelist_prompts import PROMPT_VERSION
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

_PDF_PATH = Path(__file__).resolve().parent.parent / "sample3.pdf"
_DOMAIN = "Electronics Engineering"
_TOPIC = "Design of a Wearable TDOA-based Sound Source Localization System for Assistive Spatial Awareness"
_DEFENSE_TYPE = DefenseType.CAPSTONE

_RESULTS_PATH = Path(__file__).resolve().parent / "probe_difficulty_tone_results.jsonl"
_JUDGING_PATH = Path(__file__).resolve().parent / "probe_difficulty_tone_judging.jsonl"
_KEY_PATH = Path(__file__).resolve().parent / "probe_difficulty_tone_key.jsonl"

# Fixed seed (Decision 3: "seed or explicit mapping ... recorded"). Stamped into the
# key file too, so unblinding never depends on re-running this script with the same
# environment to reproduce the shuffle.
_SHUFFLE_SEED = 20260720

_DIFFICULTY_LOW = 1
_DIFFICULTY_HIGH = 4

# Two archetypes, hand-crafted personas (Decision 3: "same persona" per pair; no
# persona-generation call is in this probe's budget). Written generically for THIS
# document (sample3, TDOA wearable capstone) — unlike probe_question_gen.py's
# _PROBE_PERSONA, which names DAZSMA by name and is not reusable across documents.
_METHODOLOGY_PERSONA = Panelist(
    archetype_key="methodology_expert",
    panelist_name="Villareal",
    icon="🔬",
    persona_framing=(
        "You are a rigorous methodologist known for pressing candidates on whether "
        "their evaluation setup actually isolates the variable they claim to be "
        "measuring, rather than confounding it with uncontrolled environmental or "
        "hardware factors."
    ),
)

_TIR_PERSONA = Panelist(
    archetype_key="technical_implementation_reviewer",
    panelist_name="Reyes",
    icon="🛠️",
    persona_framing=(
        "You are a hands-on embedded-systems reviewer known for pressing candidates "
        "on whether their hardware and signal-processing choices actually deliver "
        "the accuracy and performance they claim, not just whether the design "
        "sounds sound on paper."
    ),
)

_ARCHETYPE_PERSONAS: list[Panelist] = [_METHODOLOGY_PERSONA, _TIR_PERSONA]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile(archetype_key: str) -> DefenseProfile:
    return DefenseProfile(
        defense_type=_DEFENSE_TYPE,
        domain=_DOMAIN,
        topic=_TOPIC,
        selected_archetypes=[archetype_key],
        document_id="tone-probe",
    )


def _seeded_session(persona: Panelist, difficulty: int, excluded_chunks: list[int], chunks: list[Chunk]) -> DefenseSession:
    """A fresh session at `difficulty`, with `excluded_chunks` pre-seeded as scoreless
    turns so `_generate_question`'s retrieval excludes them — same mechanism
    `probe_question_gen.py` uses for follow-up-round chunk variety. Never appends the
    turn this call itself produces, so two calls built from the SAME excluded_chunks
    are guaranteed to retrieve the same top-1 chunk (deterministic cosine similarity,
    no sampling) — that determinism is what makes a same-chunk pair possible without
    a second, chunk-forcing retrieval path."""
    session = DefenseSession(profile=_profile(persona.archetype_key), panel=[persona], difficulty_current=difficulty)
    for idx in excluded_chunks:
        session.turns.append(
            ConversationTurn(
                panelist_archetype_key=persona.archetype_key,
                panelist_name=persona.panelist_name,
                question="seed",
                grounding_reference="seed",
                chunk_index=idx,
                chunk_text=chunks[idx].text,
                difficulty_level=difficulty,
            )
        )
    return session


def _record(turn: ConversationTurn, pair_id: str, difficulty: int, persona: Panelist) -> dict:
    grounded = is_grounded(turn.grounding_reference, turn.chunk_text)
    ratio = grounding_ratio(turn.grounding_reference, turn.chunk_text)
    return {
        "record_type": "question",
        "timestamp": _now_iso(),
        "pair_id": pair_id,
        "archetype_key": persona.archetype_key,
        "persona_name": persona.panelist_name,
        "chunk_index": turn.chunk_index,
        "difficulty_level": difficulty,
        "question": turn.question,
        "grounding_reference": turn.grounding_reference,
        "grounded": grounded,
        "grounding_ratio": round(ratio, 4),
        "grounding_retry_used": turn.grounding_retry_used,
        "grounding_flagged": turn.grounding_flagged,
    }


def main_probe() -> None:
    settings = load_settings()
    model = settings.gemini_model
    print(f"[model: {model}] [prompt_version: {PROMPT_VERSION}] [pdf: {_PDF_PATH.name}]")
    print(f"[shuffle_seed: {_SHUFFLE_SEED}]")

    texts = chunk_pdf(str(_PDF_PATH))
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    print(f"[chunks: {len(chunks)}]")

    provider = GeminiProvider(api_key=settings.gemini_api_key.get(), model=model)

    records: list[dict] = []
    pairs: list[dict] = []  # {pair_id, archetype_key, chunk_index, low_question, high_question}

    for persona in _ARCHETYPE_PERSONAS:
        excluded: list[int] = []
        print(f"\n=== archetype: {persona.archetype_key} (persona: {persona.panelist_name}) ===")
        for chunk_slot in (1, 2):
            pair_id = f"{persona.archetype_key}_chunk{chunk_slot}"

            low_session = _seeded_session(persona, _DIFFICULTY_LOW, excluded, chunks)
            low_turn = engine._generate_question(provider, low_session, chunks, embedding_model, persona, "", model)

            high_session = _seeded_session(persona, _DIFFICULTY_HIGH, excluded, chunks)
            high_turn = engine._generate_question(provider, high_session, chunks, embedding_model, persona, "", model)

            if low_turn.chunk_index != high_turn.chunk_index:
                raise RuntimeError(
                    f"Pair {pair_id}: difficulty-{_DIFFICULTY_LOW} landed on chunk "
                    f"{low_turn.chunk_index} but difficulty-{_DIFFICULTY_HIGH} landed on chunk "
                    f"{high_turn.chunk_index} — same-chunk pairing assumption violated. "
                    "Retrieval is expected deterministic given identical exclusions; "
                    "investigate before trusting any pair from this run."
                )

            low_rec = _record(low_turn, pair_id, _DIFFICULTY_LOW, persona)
            high_rec = _record(high_turn, pair_id, _DIFFICULTY_HIGH, persona)
            records.append(low_rec)
            records.append(high_rec)
            print(
                f"  {pair_id}: chunk {low_turn.chunk_index} — "
                f"diff{_DIFFICULTY_LOW} grounded={low_rec['grounded']} ratio={low_rec['grounding_ratio']} | "
                f"diff{_DIFFICULTY_HIGH} grounded={high_rec['grounded']} ratio={high_rec['grounding_ratio']}"
            )
            print(f"    [{_DIFFICULTY_LOW}] {low_turn.question}")
            print(f"    [{_DIFFICULTY_HIGH}] {high_turn.question}")

            pairs.append(
                {
                    "pair_id": pair_id,
                    "archetype_key": persona.archetype_key,
                    "chunk_index": low_turn.chunk_index,
                    "low_question": low_turn.question,
                    "high_question": high_turn.question,
                }
            )
            excluded.append(low_turn.chunk_index)

    _write_results(records, model, pairs)
    _write_judging_and_key(pairs)


def _write_results(records: list[dict], model: str, pairs: list[dict]) -> None:
    meta = {
        "record_type": "meta",
        "generated_at": _now_iso(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "document": _PDF_PATH.name,
        "grounding_threshold": 0.85,
        "difficulty_pair": [_DIFFICULTY_LOW, _DIFFICULTY_HIGH],
        "pair_count": len(pairs),
        "shuffle_seed": _SHUFFLE_SEED,
        "note": (
            "New-topic path only (Decision 3). Difficulty-4 rows' is_grounded() is the "
            "first-class check per Decision 3 — a miss here is a finding on this document, "
            "not noise. Human blind judgment lives in the paired judging/key files, not here."
        ),
    }
    with _RESULTS_PATH.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n=== is_grounded() tally (threshold 0.85) ===")
    for difficulty in (_DIFFICULTY_LOW, _DIFFICULTY_HIGH):
        rows = [r for r in records if r["difficulty_level"] == difficulty]
        passed = sum(1 for r in rows if r["grounded"])
        print(f"  difficulty {difficulty}: {passed}/{len(rows)} grounded; ratios={[r['grounding_ratio'] for r in rows]}")
    print(f"\nWrote {len(records)} question records (+1 meta) to {_RESULTS_PATH}")


def _write_judging_and_key(pairs: list[dict]) -> None:
    rnd = random.Random(_SHUFFLE_SEED)
    judging_rows: list[dict] = []
    key_rows: list[dict] = [
        {
            "record_type": "meta",
            "shuffle_seed": _SHUFFLE_SEED,
            "note": (
                "a_is_high=True means side A was the difficulty-4 question for that pair. "
                "Pass bar (Decision 3): correctly naming the difficulty-4 side in >= 3 of 4 "
                "pairs; 'indistinguishable' counts against, not as a pass."
            ),
        }
    ]

    for pair in pairs:
        a_is_high = rnd.random() < 0.5
        question_a = pair["high_question"] if a_is_high else pair["low_question"]
        question_b = pair["low_question"] if a_is_high else pair["high_question"]

        judging_rows.append(
            {
                "record_type": "judgment",
                "pair_id": pair["pair_id"],
                "question_a": question_a,
                "question_b": question_b,
                "harder_side": None,  # human-judged: "A", "B", or "indistinguishable"
                "note": None,  # human free-text
            }
        )
        key_rows.append(
            {
                "record_type": "key",
                "pair_id": pair["pair_id"],
                "archetype_key": pair["archetype_key"],
                "chunk_index": pair["chunk_index"],
                "a_is_high": a_is_high,
                "a_difficulty": _DIFFICULTY_HIGH if a_is_high else _DIFFICULTY_LOW,
                "b_difficulty": _DIFFICULTY_LOW if a_is_high else _DIFFICULTY_HIGH,
            }
        )

    with _JUDGING_PATH.open("w", encoding="utf-8") as f:
        for row in judging_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with _KEY_PATH.open("w", encoding="utf-8") as f:
        for row in key_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(judging_rows)} judging rows to {_JUDGING_PATH}")
    print(f"Wrote {len(key_rows) - 1} key rows (+1 meta) to {_KEY_PATH}")


if __name__ == "__main__":
    main_probe()
