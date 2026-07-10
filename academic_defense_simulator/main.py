"""CLI driver for Academic Defense Simulator — I/O only. Turn-loop orchestration lives
in `engine.py` (v0.3b Task 6); this module prompts for input, prints output, and calls
into the engine. No business logic beyond that boundary."""

from __future__ import annotations

import time
from uuid import uuid4

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.engine import (
    DEFAULT_CALL_DELAY,
    MAX_BLANK_ATTEMPTS,
    MAX_TURNS,
    MODEL_CALL_DELAY_SECONDS,
    _clamp_difficulty,
    _generate_question,
    _score_answer,
    select_active_panelist,
)
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import compose_full_roster, generate_panel
from academic_defense_simulator.rag.chunking import DocumentIngestionError, chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk


def _prompt_defense_type() -> tuple[DefenseType, OtherSubtype | None]:
    options = {str(i + 1): dt for i, dt in enumerate(DefenseType)}
    print("\nDefense type:")
    for key, dt in options.items():
        print(f"  {key}. {dt.value}")
    choice = input("Select [1-3]: ").strip()
    defense_type = options.get(choice, DefenseType.THESIS)

    other_subtype = None
    if defense_type == DefenseType.OTHER:
        sub_options = {str(i + 1): st for i, st in enumerate(OtherSubtype)}
        print("\nSubtype:")
        for key, st in sub_options.items():
            print(f"  {key}. {st.value}")
        sub_choice = input(f"Select [1-{len(sub_options)}]: ").strip()
        other_subtype = sub_options.get(sub_choice, OtherSubtype.ORAL_COMPS)

    return defense_type, other_subtype


def _prompt_answer() -> str:
    """Re-prompt in place on a blank answer — doesn't consume a turn, doesn't hit
    the scorer with empty input. After MAX_BLANK_ATTEMPTS consecutive blanks, give
    up and let the blank through as a genuine non-answer (real signal at that point)."""
    for attempt in range(1, MAX_BLANK_ATTEMPTS + 1):
        answer = input("Your answer: ").strip()
        if answer:
            return answer
        if attempt < MAX_BLANK_ATTEMPTS:
            print("Answer cannot be blank — please respond.")
        else:
            print(f"No answer given after {MAX_BLANK_ATTEMPTS} attempts — proceeding as a non-answer.")
    return ""


def main() -> None:
    settings = load_settings()

    # 1. PDF path
    pdf_path = input("PDF path: ").strip()

    # 2. Defense context
    defense_type, other_subtype = _prompt_defense_type()
    domain = input("Domain / discipline: ").strip()
    topic = input("Research title / topic: ").strip()

    # 3-4. Build profile
    document_id = str(uuid4())
    profile = DefenseProfile(
        defense_type=defense_type,
        other_subtype=other_subtype,
        domain=domain,
        topic=topic,
        document_id=document_id,
    )

    # 5. Chunk PDF
    print("\nProcessing document...")
    try:
        texts = chunk_pdf(pdf_path)
    except DocumentIngestionError as exc:
        print(f"\nCould not process document: {exc}")
        return

    # 6. Embed chunks
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]

    other_subtype_line = (
        f"\n- Defense subtype: {profile.other_subtype.value}" if profile.other_subtype is not None else ""
    )

    print(f"[model: {settings.gemini_model}]")
    provider = GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)

    archetype_roster = compose_full_roster(profile)
    panel, fallback_used = generate_panel(profile, archetype_roster, provider)
    print(f"[panel: {[p.archetype_key for p in panel]} — fallback_used={fallback_used}]")

    session = DefenseSession(profile=profile, panel=panel, difficulty_current=profile.difficulty_start)

    try:
        for turn_num in range(1, MAX_TURNS + 1):
            active_panelist = select_active_panelist(session, turn_num)
            print(f"\n=== Turn {turn_num}/{MAX_TURNS} (difficulty {session.difficulty_current}/5) ===")

            turn = _generate_question(
                provider, session, chunks, embedding_model, active_panelist, other_subtype_line, settings.gemini_model
            )

            print(f"\nDr. {active_panelist.panelist_name}: {turn.question}\n")
            print(f"[Grounding: \"{turn.grounding_reference}\" — difficulty {turn.difficulty_level}/5]\n")

            turn.answer = _prompt_answer()

            turn.score = _score_answer(provider, turn, active_panelist, profile.defense_type.value)
            print(
                f"[Score — clarity {turn.score.clarity}, depth {turn.score.depth}, "
                f"grounding {turn.score.grounding}, difficulty_delta {turn.score.difficulty_delta}, "
                f"primary_gap: {turn.score.primary_gap!r}]"
            )

            session.turns.append(turn)
            session.difficulty_current = _clamp_difficulty(session.difficulty_current + turn.score.difficulty_delta)

            if turn_num != MAX_TURNS:
                time.sleep(MODEL_CALL_DELAY_SECONDS.get(settings.gemini_model, DEFAULT_CALL_DELAY))
    except LLMProviderError as exc:
        print(f"\nSession aborted: {exc}")
        return

    print("\nSession ended.")


if __name__ == "__main__":
    main()
