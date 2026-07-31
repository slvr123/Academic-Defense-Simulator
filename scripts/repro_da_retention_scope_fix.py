"""v0.3i live re-run: reproduces the DA retention-scope bug's exact shape against the
fix, on the real document, real provider, real weak answers -- no mocks.

Same measurement conditions as the v0.3h session that surfaced the bug: a single
domain panelist (methodology_expert) + Devil's Advocate, weak answers throughout, so
DA's contested chunk is guaranteed to collide with the domain panelist's own
just-exhausted chunk (DA always retargets the highest-scored prior claim, and with one
domain panelist and uniformly weak/tied scores that's always its first turn's chunk).
Under the pre-fix code this produced exactly 4 turns / 12 calls, DA's own chain
denied, session ending on neither documented condition. Post-fix, DA should get its
own follow-up chain and the session should run longer.

Run: python scripts/repro_da_retention_scope_fix.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import academic_defense_simulator.engine as engine
from academic_defense_simulator.config import load_settings
from academic_defense_simulator.document_profile import extract_document_profile
from academic_defense_simulator.document_relevance import assess_document
from academic_defense_simulator.engine import select_active_panelist, session_is_complete, t_max
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import CallCounter
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY, compose_full_roster, generate_panel
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk
from academic_defense_simulator.report import build_report

_PDF_PATH = Path(__file__).resolve().parent.parent / "Group2_Library Management System for DAZSMA Documentation (1).pdf"
_RELEVANCE_MODEL = "gemini-2.5-flash"

# Same Day-5 weak-answer pattern as scripts/probe_question_gen.py -- vague, numberless,
# non-committal, engineered to fail the "strong" gate every time.
_WEAK_ANSWER = (
    "We felt it was a reasonable choice given our timeline, and the results seemed to line "
    "up with what we expected, so we think it holds up fine for a project of this scope."
)


def _labeled_provider(settings, model: str, counter: CallCounter, label: str) -> GeminiProvider:
    return GeminiProvider(api_key=settings.gemini_api_key.get(), model=model, call_counter=counter, label=label)


def _pace(model: str) -> None:
    time.sleep(engine.MODEL_CALL_DELAY_SECONDS.get(model, engine.DEFAULT_CALL_DELAY))


def main() -> None:
    settings = load_settings()
    model = settings.gemini_model
    print(f"[model: {model}] [pdf: {_PDF_PATH.name}]")

    counter = CallCounter()

    texts = chunk_pdf(str(_PDF_PATH))
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]
    print(f"[chunks: {len(chunks)}]")

    # --- Setup stage 1: relevance gate ---
    gate_provider = _labeled_provider(settings, _RELEVANCE_MODEL, counter, "relevance gate")
    assessment = assess_document(texts, gate_provider)
    _pace(_RELEVANCE_MODEL)
    print(f"[gate] is_defense_material={assessment.is_defense_material} kind={assessment.document_kind!r}")

    # --- Setup stage 2: domain/topic extraction ---
    extraction_provider = _labeled_provider(settings, model, counter, "domain/topic extraction")
    extraction = extract_document_profile(texts, extraction_provider)
    _pace(model)
    print(f"[extraction] domain={extraction.domain!r} topic={extraction.topic!r}")

    profile = DefenseProfile(
        defense_type=DefenseType.CAPSTONE,
        domain=extraction.domain or "library and information science",
        topic=extraction.topic or "Library Management System for DAZSMA",
        selected_archetypes=["methodology_expert"],  # single domain panelist, matches the measurement shape
        document_id="da-retention-repro",
    )

    # --- Setup stage 3: persona generation ---
    persona_provider = _labeled_provider(settings, model, counter, "persona generation")
    roster = compose_full_roster(profile)
    panel, fallback_used = generate_panel(profile, roster, persona_provider)
    _pace(model)
    print(f"[persona] roster={roster} fallback_used={fallback_used} "
          f"names={[p.panelist_name for p in panel]}")

    session = DefenseSession(profile=profile, panel=panel, difficulty_current=profile.difficulty_start)

    # --- Turn loop: real question generation + real scoring, weak answer every time ---
    turn_num = 0
    while not session_is_complete(session):
        turn_num += 1
        if turn_num > t_max(session):
            print("[abort] t_max backstop exceeded in the driver loop -- should never happen.")
            break

        active_panelist = select_active_panelist(session)
        q_provider = _labeled_provider(settings, model, counter, "question generation")
        turn = engine._generate_question(
            q_provider, session, chunks, embedding_model, active_panelist, "", model
        )
        _pace(model)

        turn.answer = _WEAK_ANSWER
        s_provider = _labeled_provider(settings, model, counter, "answer scoring")
        turn.score = engine._score_answer(s_provider, turn, active_panelist, profile.defense_type.value)
        _pace(model)

        session.turns.append(turn)

        is_da = active_panelist.archetype_key == DEVILS_ADVOCATE_KEY
        print(
            f"  turn {turn_num}: {'DA' if is_da else active_panelist.archetype_key} "
            f"chunk={turn.chunk_index} score=({turn.score.clarity},{turn.score.depth},{turn.score.grounding}) "
            f"follow_ups_on_current_topic={session.follow_ups_on_current_topic} "
            f"should_follow_up_next={engine._should_follow_up(session)}"
        )

    print(f"\n[result] total turns: {len(session.turns)}")
    da_turns = [t for t in session.turns if t.panelist_archetype_key == DEVILS_ADVOCATE_KEY]
    print(f"[result] DA's own chain length: {len(da_turns)}")
    if da_turns:
        da_chunks = {t.chunk_index for t in da_turns}
        print(f"[result] DA's chunk(s): {sorted(da_chunks)}")

    last_turn = session.turns[-1]
    if len(session.turns) >= t_max(session):
        end_reason = "t_max backstop (Decision 6)"
    elif last_turn.panelist_archetype_key == DEVILS_ADVOCATE_KEY:
        end_reason = "DA's own chain concluded (Decision 5) -- strong answer or DA's own cap reached"
    else:
        end_reason = "UNDOCUMENTED -- session ended without DA having spoken last (should not happen)"
    print(f"[result] end condition: {end_reason}")

    # --- Report narrative call, mirroring the real export flow ---
    narrative_provider = _labeled_provider(settings, model, counter, "report narrative")
    session.report = build_report(session, narrative_provider)
    _pace(model)

    print(f"\n[CallCounter] total={counter.total}")
    for stage, n in counter.by_stage.items():
        print(f"  {stage}: {n}")


if __name__ == "__main__":
    main()
