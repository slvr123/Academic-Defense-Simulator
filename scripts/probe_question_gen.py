"""Question-generation probe (v0.2.5 hardening, Task 4; re-run for 0.3a Task 7).

Formal re-verification that flash-lite question generation holds to the Day 3 bar, on
BOTH prompt paths — new-topic (PANELIST_SYSTEM_PROMPT) and follow-up
(FOLLOWUP_SYSTEM_PROMPT). Question generation was only sanity-checked when scoring moved
to flash-lite; this is the evidence.

Reuses the real orchestration helpers in `engine.py` (retrieval, prompt rendering, scoring,
branching) so what runs here is what runs in a session — not a re-implementation. Per
question it records raw model output to a results JSONL:
  (a) grounded  — automated, via grounding.is_grounded() against the turn's chunk
  (b) in_lane / (c) difficulty_ok — human-judged, left null for Sean to fill in

Follow-up path grounding is expected to run lower than new-topic: the v0.2 eval
(`docs/v0.2-eval-results.md` Finding C) found follow-up `grounding_reference` often echoes
the candidate's own prior answer rather than the source document. That is the finding to
confirm here, not a bug in the check — the 0.85 threshold and is_grounded() are NOT tuned
on the strength of this run.

0.3a Task 7 change (flagged per the brief's "minimal, flagged" instruction): main_probe()
now takes `persona` and `results_path` parameters so the same script can target the new
0.3 templates with a real generated persona without touching the irreplaceable v0.2 JSONL.
`_generate_question`/`_score_answer`'s signature changed from `archetype: dict` to
`panelist: Panelist` in Task 4 (0.3a) — this script's calls follow that.

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

from typing import Optional

import academic_defense_simulator.engine as engine
from academic_defense_simulator.config import load_settings
from academic_defense_simulator.grounding import grounding_ratio, is_grounded
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.prompts.panelist_prompts import PROMPT_VERSION
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk

_ARCHETYPE_KEY = "methodology_expert"  # same archetype as all prior evidence — no new variables
_PDF_PATH = Path(__file__).resolve().parent.parent / "Group2_Library Management System for DAZSMA Documentation (1).pdf"
_RESULTS_PATH_V03 = Path(__file__).resolve().parent / "probe_question_gen_v0.3_results.jsonl"
_DEFENSE_TYPE = DefenseType.CAPSTONE

# Real generated persona from this session's live Task 3 verify call (thesis, panel_size 3)
# — reused here rather than regenerated, per the Task 7 brief, so the probe exercises real
# persona_framing injection without spending an extra persona-generation call.
_PROBE_PERSONA = Panelist(
    archetype_key=_ARCHETYPE_KEY,
    panelist_name="Okonkwo",
    persona_framing=(
        "You are a quantitative systems analyst known for your rigorous skepticism "
        "regarding data integrity and software validation metrics. You will press the "
        "candidate on whether their evaluation framework for the DAZSMA system truly "
        "isolates the impact of the new library management tools from external "
        "environmental variables."
    ),
)

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


def _session(difficulty_current: int, persona: Panelist) -> DefenseSession:
    return DefenseSession(profile=_profile(), panel=[persona], difficulty_current=difficulty_current)


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


def main_probe(persona: Panelist = _PROBE_PERSONA, results_path: Path = _RESULTS_PATH_V03) -> None:
    settings = load_settings()
    model = settings.gemini_model
    print(f"[model: {model}] [prompt_version: {PROMPT_VERSION}] [persona: {persona.panelist_name}]")
    print(f"[pdf: {_PDF_PATH.name}]")

    texts = chunk_pdf(str(_PDF_PATH))
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    print(f"[chunks: {len(chunks)}]")

    provider = GeminiProvider(api_key=settings.gemini_api_key, model=model)
    pacing = engine.MODEL_CALL_DELAY_SECONDS.get(model, engine.DEFAULT_CALL_DELAY)

    records: list[dict] = []

    # --- New-topic path: 4 questions, forced across distinct chunks, difficulties 2/3 only
    # (0.3a Task 7: difficulty 4 stays parked for 0.3b, per the standing flag). ---
    # A session with only scoreless turns keeps _should_follow_up False, so every call takes
    # the new-topic branch; appending each turn adds its chunk to used_chunk_indices, so the
    # next retrieve() excludes it — the real loop's variety mechanism.
    print("\n=== New-topic path ===")
    nt_session = _session(2, persona)
    for requested in (2, 3, 2, 3):
        nt_session.difficulty_current = requested
        turn = engine._generate_question(
            provider, nt_session, chunks, embedding_model, persona, "", model
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
        fu_session = _session(3, persona)
        # Seed scoreless turns on already-used parent chunks so this round's new-topic picks
        # a different chunk (nicer variety; not strictly required by the brief).
        for idx in excluded_parent_chunks:
            fu_session.turns.append(
                ConversationTurn(panelist_archetype_key=persona.archetype_key, panelist_name=persona.panelist_name,
                                 question="seed", grounding_reference="seed", chunk_index=idx,
                                 chunk_text=chunks[idx].text, difficulty_level=3)
            )

        parent = engine._generate_question(provider, fu_session, chunks, embedding_model, persona, "", model)
        fu_session.turns.append(parent)
        excluded_parent_chunks.append(parent.chunk_index)

        parent.answer = _WEAK_ANSWER
        parent.score = engine._score_answer(provider, parent, persona, _DEFENSE_TYPE.value)
        time.sleep(pacing)  # _score_answer doesn't pace itself; keep RPM safe before the next call
        is_strong = engine._is_strong_answer(parent.score)
        fires = engine._should_follow_up(fu_session)
        print(f"  round {round_num}: parent chunk {parent.chunk_index}, "
              f"primary_gap={parent.score.primary_gap!r}, is_strong={is_strong}, follow_up_fires={fires}")

        if not fires:
            print(f"  round {round_num}: follow-up did not fire (answer scored strong or no gap) — "
                  f"recording parent only, skipping follow-up generation.")
            records.append(_record_for(parent, "new_topic", 3,
                                       note="intended as follow-up parent; follow-up gate did not fire"))
            continue

        follow_up = engine._generate_question(provider, fu_session, chunks, embedding_model, persona, "", model)
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

    _write_results(records, model, persona, results_path)


def _write_results(records: list[dict], model: str, persona: Panelist, results_path: Path) -> None:
    meta = {
        "record_type": "meta",
        "generated_at": _now_iso(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "archetype": persona.archetype_key,
        "persona_name": persona.panelist_name,
        "persona_framing": persona.persona_framing,
        "grounding_threshold": 0.85,
        "document": _PDF_PATH.name,
        "difficulty_levels_probed": "2, 3 only — difficulty 4 stays parked for 0.3b per the standing flag",
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
    with results_path.open("w", encoding="utf-8") as f:
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
    print(f"\nWrote {len(records)} question records (+1 meta) to {results_path}")


# ============================================================================
# v0.3 hardening probe (Decision 1's resolution procedure, Decision 7): forced
# difficulty 4/5 across all three question paths, to hunt for the fabrication the
# is_grounded() enforcement fix (Decisions 2-4) is supposed to catch. See
# docs/v0.3-hardening-decisions.md and docs/briefs/v0.3-hardening-brief.md Task 2.
# ============================================================================

_RESULTS_PATH_HARDENING = Path(__file__).resolve().parent / "probe_question_gen_v0.3_hardening_results.jsonl"

# Devil's Advocate persona for the fixture case (Decision 6) — same voice/rigor register
# as the methodology persona above, just the DA archetype.
_DA_PERSONA = Panelist(
    archetype_key=DEVILS_ADVOCATE_KEY,
    panelist_name="Villanueva",
    persona_framing=(
        "You are a skeptical senior reviewer known for pressure-testing the single "
        "strongest claim a candidate has made, never letting a confident number pass "
        "unchallenged."
    ),
)

# Small hand-built synthetic digest (Decision 6): one clear, strong prior claim, rendered
# directly as DA's target turn — isolates DA's high-difficulty behavior against a known
# claim without spending calls driving three other panelists through a full session first.
_SYNTHETIC_STRONG_CLAIM_TURN = ConversationTurn(
    panelist_archetype_key="methodology_expert",
    panelist_name="Okonkwo",
    question="How did you validate that the new checkout workflow actually reduced retrieval time?",
    grounding_reference="the average book retrieval time dropped from 4.2 minutes to 1.5 minutes",
    chunk_index=0,
    chunk_text=(
        "System evaluation. To assess the effectiveness of the new barcode-based checkout "
        "workflow, the researchers timed 50 retrieval transactions before and after "
        "deployment. Under the manual card-catalog process, the average book retrieval "
        "time was 4.2 minutes. After deployment of the DAZSMA system, the average book "
        "retrieval time dropped from 4.2 minutes to 1.5 minutes, a reduction attributed "
        "to the barcode scanning and digital shelf-location lookup features."
    ),
    difficulty_level=2,
    answer=(
        "We timed 50 retrieval transactions before and after deployment and found the "
        "average retrieval time dropped from 4.2 minutes to 1.5 minutes."
    ),
    score=AnswerScore(
        clarity=5,
        depth=4,
        grounding=5,
        difficulty_delta=0,
        primary_gap=None,
        answer_summary=(
            "The candidate claimed the new barcode-based checkout workflow reduced average "
            "book retrieval time from 4.2 minutes to 1.5 minutes, measured across 50 "
            "transactions before and after deployment."
        ),
    ),
)


def _generate_forced(
    provider,
    session: DefenseSession,
    chunks: list[Chunk],
    embedding_model: EmbeddingModel,
    persona: Panelist,
    other_subtype_line: str,
    model: str,
    forced_difficulty: Optional[int] = None,
) -> ConversationTurn:
    """Thin wrapper over `engine._generate_question` (Decision 7): when set,
    `forced_difficulty` overrides the naturally-computed difficulty and is injected
    directly into both prompt paths' `{difficulty_level}` slot via `session.difficulty_current`
    -- the same value the real loop would have used, just forced instead of computed.
    Routes to the Devil's Advocate path automatically when `persona` is the DA archetype,
    same as the real loop."""
    if forced_difficulty is not None:
        session.difficulty_current = forced_difficulty
    return engine._generate_question(provider, session, chunks, embedding_model, persona, other_subtype_line, model)


def _record_hardening(turn: ConversationTurn, path: str, forced_difficulty: int, **extra) -> dict:
    grounded = is_grounded(turn.grounding_reference, turn.chunk_text)
    ratio = grounding_ratio(turn.grounding_reference, turn.chunk_text)
    record = {
        "record_type": "question",
        "timestamp": _now_iso(),
        "path": path,
        "chunk_index": turn.chunk_index,
        "forced_difficulty": forced_difficulty,
        "returned_difficulty_level": turn.difficulty_level,
        "question": turn.question,
        "grounding_reference": turn.grounding_reference,
        "grounded": grounded,
        "grounding_ratio": round(ratio, 4),
        "grounding_retry_used": turn.grounding_retry_used,
        "grounding_flagged": turn.grounding_flagged,
        "content_fabricated_despite_grounding": None,  # human-judged (Decision 1's actual test)
    }
    if path == "da_fixture":
        record["digest_claim_fidelity"] = None  # human-judged (Decision 6), da_fixture rows only
    record.update(extra)
    return record


def main_probe_hardening(
    persona: Panelist = _PROBE_PERSONA, results_path: Path = _RESULTS_PATH_HARDENING
) -> None:
    settings = load_settings()
    model = settings.gemini_model
    print(f"[model: {model}] [prompt_version: {PROMPT_VERSION}] [persona: {persona.panelist_name}]")
    print(f"[pdf: {_PDF_PATH.name}]")

    texts = chunk_pdf(str(_PDF_PATH))
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    print(f"[chunks: {len(chunks)}]")

    provider = GeminiProvider(api_key=settings.gemini_api_key, model=model)

    records: list[dict] = []

    # --- New-topic path: >= 4 questions, forced across difficulty 4 and 5, >= 3 distinct
    # chunks (exclude_indices forces variety, same mechanism as the real loop). ---
    print("\n=== New-topic path (forced difficulty 4/5) ===")
    nt_session = _session(2, persona)
    for forced in (4, 5, 4, 5):
        turn = _generate_forced(provider, nt_session, chunks, embedding_model, persona, "", model, forced)
        rec = _record_hardening(turn, "new_topic", forced)
        records.append(rec)
        print(f"  chunk {turn.chunk_index} @forced-diff{forced}: grounded={rec['grounded']} "
              f"ratio={rec['grounding_ratio']} retry_used={turn.grounding_retry_used} "
              f"flagged={turn.grounding_flagged} — {turn.question[:70]}...")
        nt_session.turns.append(turn)  # score stays None -> next call stays new-topic, excludes this chunk
    distinct_chunks = {r["chunk_index"] for r in records if r["path"] == "new_topic"}
    print(f"  distinct chunks used: {sorted(distinct_chunks)} ({len(distinct_chunks)} total)")

    # --- Follow-up path: >= 2 rounds, forced via real turns (new-topic parent -> weak
    # answer -> score -> follow-up), forced difficulty 4 then 5. ---
    print("\n=== Follow-up path (forced difficulty 4/5) ===")
    excluded_parent_chunks: list[int] = list(distinct_chunks)
    for round_num, forced in enumerate((4, 5), start=1):
        fu_session = _session(forced, persona)
        for idx in excluded_parent_chunks:
            fu_session.turns.append(
                ConversationTurn(panelist_archetype_key=persona.archetype_key, panelist_name=persona.panelist_name,
                                 question="seed", grounding_reference="seed", chunk_index=idx,
                                 chunk_text=chunks[idx].text, difficulty_level=forced)
            )

        parent = _generate_forced(provider, fu_session, chunks, embedding_model, persona, "", model, forced)
        fu_session.turns.append(parent)
        excluded_parent_chunks.append(parent.chunk_index)

        parent.answer = _WEAK_ANSWER
        parent.score = engine._score_answer(provider, parent, persona, _DEFENSE_TYPE.value)
        time.sleep(engine.MODEL_CALL_DELAY_SECONDS.get(model, engine.DEFAULT_CALL_DELAY))
        fires = engine._should_follow_up(fu_session)
        print(f"  round {round_num}: parent chunk {parent.chunk_index}, "
              f"primary_gap={parent.score.primary_gap!r}, follow_up_fires={fires}")

        records.append(_record_hardening(parent, "new_topic", forced, note="follow-up parent turn"))

        if not fires:
            print(f"  round {round_num}: follow-up gate did not fire — recording parent only.")
            continue

        follow_up = _generate_forced(provider, fu_session, chunks, embedding_model, persona, "", model, forced)
        rec = _record_hardening(
            follow_up, "follow_up", forced,
            parent_chunk_index=parent.chunk_index,
            parent_question=parent.question,
            scored_answer=_WEAK_ANSWER,
            primary_gap=parent.score.primary_gap,
        )
        records.append(rec)
        print(f"  round {round_num}: follow-up on chunk {follow_up.chunk_index} @forced-diff{forced}: "
              f"grounded={rec['grounded']} ratio={rec['grounding_ratio']} "
              f"retry_used={follow_up.grounding_retry_used} flagged={follow_up.grounding_flagged} "
              f"— {follow_up.question[:70]}...")

    # --- DA fixture: >= 2 generations against the synthetic strong-claim digest, forced
    # difficulty 5, DA persona/prompt path (Decision 6). ---
    print("\n=== Devil's Advocate fixture (forced difficulty 5, synthetic digest) ===")
    for i in range(2):
        da_session = DefenseSession(
            profile=_profile(), panel=[_DA_PERSONA], difficulty_current=5,
            turns=[_SYNTHETIC_STRONG_CLAIM_TURN],
        )
        da_turn = _generate_forced(provider, da_session, chunks, embedding_model, _DA_PERSONA, "", model, 5)
        rec = _record_hardening(
            da_turn, "da_fixture", 5,
            target_claim_summary=_SYNTHETIC_STRONG_CLAIM_TURN.score.answer_summary,
            target_chunk_text=_SYNTHETIC_STRONG_CLAIM_TURN.chunk_text,
        )
        records.append(rec)
        print(f"  generation {i + 1}: grounded={rec['grounded']} ratio={rec['grounding_ratio']} "
              f"retry_used={da_turn.grounding_retry_used} flagged={da_turn.grounding_flagged} "
              f"— {da_turn.question[:90]}...")

    _write_hardening_results(records, model, persona, results_path)


def _write_hardening_results(records: list[dict], model: str, persona: Panelist, results_path: Path) -> None:
    meta = {
        "record_type": "meta",
        "generated_at": _now_iso(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "archetype": persona.archetype_key,
        "persona_name": persona.panelist_name,
        "grounding_threshold": 0.85,
        "document": _PDF_PATH.name,
        "difficulty_levels_probed": "4, 5 forced — this is Decision 1's resolution procedure, not a threshold retune",
        "human_judged_columns": (
            "content_fabricated_despite_grounding (all rows); digest_claim_fidelity (da_fixture rows only)"
        ),
    }
    with results_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n=== is_grounded() tally by path (threshold 0.85) ===")
    for path in ("new_topic", "follow_up", "da_fixture"):
        rows = [r for r in records if r["path"] == path]
        if not rows:
            continue
        passed = sum(1 for r in rows if r["grounded"])
        retried = sum(1 for r in rows if r["grounding_retry_used"])
        flagged = sum(1 for r in rows if r["grounding_flagged"])
        print(f"  {path}: {passed}/{len(rows)} grounded; retried={retried}; flagged={flagged}; "
              f"ratios={[r['grounding_ratio'] for r in rows]}")
    print(f"\nWrote {len(records)} question records (+1 meta) to {results_path}")


# ============================================================================
# v0.3d follow-up attribution probe (docs/v0.3d-followup-attribution-fix.md, Decision 4):
# re-verifies the follow-up path only, now split by {prior_exchange_framing} case
# (same_asker / colleague). Harness extension, fixture-level only: `_generate_question`'s
# follow-up branch already takes the presser as an explicit `panelist` argument rather than
# deriving it from round-robin, so forcing asker != presser is just passing a second persona
# to the follow-up call -- no engine.py change, no touching select_active_panelist.
# ============================================================================

_RESULTS_PATH_FOLLOWUP_ATTRIBUTION = Path(__file__).resolve().parent / "probe_followup_attribution_results.jsonl"

# Second persona, distinct archetype from _PROBE_PERSONA (methodology_expert), used as the
# follow-up presser in colleague-case rounds -- asker (parent question) stays _PROBE_PERSONA.
_COLLEAGUE_PERSONA = Panelist(
    archetype_key="literature_theory_specialist",
    panelist_name="Delacroix",
    persona_framing=(
        "You are known for tracing every claim back to its theoretical grounding and "
        "pressing candidates on citations or framing that don't hold up under scrutiny."
    ),
)


def _record_followup_attribution(turn: ConversationTurn, framing_case: str, parent: ConversationTurn, **extra) -> dict:
    grounded = is_grounded(turn.grounding_reference, turn.chunk_text)
    ratio = grounding_ratio(turn.grounding_reference, turn.chunk_text)
    record = {
        "record_type": "question",
        "timestamp": _now_iso(),
        "path": "follow_up",
        "framing_case": framing_case,
        "asker_archetype_key": parent.panelist_archetype_key,
        "asker_panelist_name": parent.panelist_name,
        "presser_archetype_key": turn.panelist_archetype_key,
        "presser_panelist_name": turn.panelist_name,
        "chunk_index": turn.chunk_index,
        "question": turn.question,
        "grounding_reference": turn.grounding_reference,
        "grounded": grounded,
        "grounding_ratio": round(ratio, 4),
        # Human-judged, per the brief's colleague-case pass bar:
        "false_first_person_claim": None,  # (1) no first-person claim of having asked the prior question
        "presses_identified_weakness": None,  # (2) the question still presses the identified weakness
        "in_lane": None,
        "difficulty_ok": None,
    }
    record.update(extra)
    return record


def main_probe_followup_attribution(
    asker_persona: Panelist = _PROBE_PERSONA,
    colleague_persona: Panelist = _COLLEAGUE_PERSONA,
    results_path: Path = _RESULTS_PATH_FOLLOWUP_ATTRIBUTION,
) -> None:
    settings = load_settings()
    model = settings.gemini_model
    print(f"[model: {model}] [prompt_version: {PROMPT_VERSION}] "
          f"[asker: {asker_persona.panelist_name}] [colleague: {colleague_persona.panelist_name}]")
    print(f"[pdf: {_PDF_PATH.name}]")

    texts = chunk_pdf(str(_PDF_PATH))
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    print(f"[chunks: {len(chunks)}]")

    provider = GeminiProvider(api_key=settings.gemini_api_key, model=model)
    pacing = engine.MODEL_CALL_DELAY_SECONDS.get(model, engine.DEFAULT_CALL_DELAY)

    records: list[dict] = []
    excluded_parent_chunks: list[int] = []

    # 2 same-asker rounds (presser == asker, unchanged wording) + 2 colleague rounds
    # (presser != asker) -- Decision 4's minimum.
    rounds = [
        ("same_asker", asker_persona),
        ("same_asker", asker_persona),
        ("colleague", colleague_persona),
        ("colleague", colleague_persona),
    ]

    for round_num, (framing_case, presser) in enumerate(rounds, start=1):
        fu_session = _session(3, asker_persona)
        for idx in excluded_parent_chunks:
            fu_session.turns.append(
                ConversationTurn(panelist_archetype_key=asker_persona.archetype_key, panelist_name=asker_persona.panelist_name,
                                 question="seed", grounding_reference="seed", chunk_index=idx,
                                 chunk_text=chunks[idx].text, difficulty_level=3)
            )

        parent = engine._generate_question(provider, fu_session, chunks, embedding_model, asker_persona, "", model)
        fu_session.turns.append(parent)
        excluded_parent_chunks.append(parent.chunk_index)

        parent.answer = _WEAK_ANSWER
        parent.score = engine._score_answer(provider, parent, asker_persona, _DEFENSE_TYPE.value)
        time.sleep(pacing)  # _score_answer doesn't pace itself; keep RPM safe before the next call
        fires = engine._should_follow_up(fu_session)
        print(f"  round {round_num} [{framing_case}]: parent chunk {parent.chunk_index} "
              f"(asker {parent.panelist_archetype_key}), primary_gap={parent.score.primary_gap!r}, "
              f"follow_up_fires={fires}")

        if not fires:
            print(f"  round {round_num} [{framing_case}]: follow-up did not fire -- no row recorded for this case.")
            continue

        follow_up = engine._generate_question(provider, fu_session, chunks, embedding_model, presser, "", model)
        rec = _record_followup_attribution(
            follow_up, framing_case, parent,
            parent_question=parent.question,
            scored_answer=_WEAK_ANSWER,
            primary_gap=parent.score.primary_gap,
        )
        records.append(rec)
        print(f"  round {round_num} [{framing_case}]: follow-up by {presser.panelist_name} "
              f"({presser.archetype_key}) on chunk {follow_up.chunk_index}: grounded={rec['grounded']} "
              f"ratio={rec['grounding_ratio']} -- {follow_up.question[:90]}...")
        time.sleep(pacing)

    _write_followup_attribution_results(records, model, asker_persona, colleague_persona, results_path)


def _write_followup_attribution_results(
    records: list[dict], model: str, asker_persona: Panelist, colleague_persona: Panelist, results_path: Path
) -> None:
    meta = {
        "record_type": "meta",
        "generated_at": _now_iso(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "asker_archetype": asker_persona.archetype_key,
        "asker_name": asker_persona.panelist_name,
        "colleague_archetype": colleague_persona.archetype_key,
        "colleague_name": colleague_persona.panelist_name,
        "grounding_threshold": 0.85,
        "document": _PDF_PATH.name,
        "note": (
            "Re-verification for docs/v0.3d-followup-attribution-fix.md Decision 4 -- follow-up "
            "path only. framing_case is same_asker or colleague, forced fixture-level by passing "
            "a different `panelist` argument to the follow-up call than the parent call, not by "
            "changing engine.py's round-robin."
        ),
        "human_judged_columns": "false_first_person_claim, presses_identified_weakness, in_lane, difficulty_ok",
    }
    with results_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n=== Grounding tally by framing_case (threshold 0.85) ===")
    for case in ("same_asker", "colleague"):
        rows = [r for r in records if r["framing_case"] == case]
        passed = sum(1 for r in rows if r["grounded"])
        print(f"  {case}: {passed}/{len(rows)} grounded; ratios={[r['grounding_ratio'] for r in rows]}")
    print(f"\nWrote {len(records)} question records (+1 meta) to {results_path}")


if __name__ == "__main__":
    main_probe()
