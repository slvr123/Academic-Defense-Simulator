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
    MODEL_CALL_DELAY_SECONDS,
    _clamp_difficulty,
    _generate_question,
    _score_answer,
    select_active_panelist,
    session_is_complete,
)
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import LLMProviderError
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.report import DefenseReport
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import PANEL_COMPOSITION, compose_full_roster, generate_panel
from academic_defense_simulator.rag.chunking import DocumentIngestionError, chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk
from academic_defense_simulator.report import build_report


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

    # 3-4. Build profile — selected_archetypes replaces panel_size (v0.3e). This is a
    # dev driver, not product UX, so it doesn't prompt for a selection: default to the
    # first (highest-priority) archetype in PANEL_COMPOSITION for the chosen type.
    composition_key = (
        f"other/{other_subtype.value}" if defense_type == DefenseType.OTHER else defense_type.value
    )
    default_archetype = PANEL_COMPOSITION[composition_key][0]
    document_id = str(uuid4())
    profile = DefenseProfile(
        defense_type=defense_type,
        other_subtype=other_subtype,
        domain=domain,
        topic=topic,
        selected_archetypes=[default_archetype],
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
    provider = GeminiProvider(api_key=settings.gemini_api_key.get(), model=settings.gemini_model)

    archetype_roster = compose_full_roster(profile)
    panel, fallback_used = generate_panel(profile, archetype_roster, provider)
    print(f"[panel: {[p.archetype_key for p in panel]} — fallback_used={fallback_used}]")

    session = DefenseSession(profile=profile, panel=panel, difficulty_current=profile.difficulty_start)

    try:
        turn_num = 0
        while not session_is_complete(session):
            turn_num += 1
            active_panelist = select_active_panelist(session)
            print(f"\n=== Turn {turn_num} (difficulty {session.difficulty_current}/5) ===")

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

            if not session_is_complete(session):
                time.sleep(MODEL_CALL_DELAY_SECONDS.get(settings.gemini_model, DEFAULT_CALL_DELAY))
    except LLMProviderError as exc:
        print(f"\nSession aborted: {exc}")
        return

    print("\nSession ended.")

    # v1.0b-2: suggestions is pinned to gemini-2.5-flash regardless of GEMINI_MODEL
    # (Decision 2 — a judgment task, same rule as the relevance gate). This CLI driver
    # has no separate key/mode gate to resolve a provider from, so it reuses the same
    # API key as the narrative/session provider, just a different model.
    suggestions_provider = GeminiProvider(api_key=settings.gemini_api_key.get(), model="gemini-2.5-flash")
    session.report = build_report(session, provider, suggestions_provider)
    _print_report(session.report)


def _print_report(report: DefenseReport) -> None:
    """Plain-text rendering of the numeric report — no new dependency (report
    *rendering* in a UI is 0.3d; this is just the CLI's own output)."""
    print("\n=== Defense Report ===")
    print(f"Difficulty trajectory: {report.difficulty_trajectory}")
    print(
        f"Overall — clarity: {report.overall_avg_clarity:.2f}, "
        f"depth: {report.overall_avg_depth:.2f}, grounding: {report.overall_avg_grounding:.2f}"
    )

    print("\nPer-panelist:")
    for section in report.panelist_sections:
        print(f"  Dr. {section.panelist_name} ({section.archetype_key}) — {section.turns_taken} turn(s)")
        if section.turns_taken:
            print(
                f"    avg clarity: {section.avg_clarity:.2f}, avg depth: {section.avg_depth:.2f}, "
                f"avg grounding: {section.avg_grounding:.2f}"
            )
            for gap in section.primary_gaps:
                print(f"    gap: {gap}")

    print("\nPushback events:")
    if not report.pushback_events:
        print("  (none — difficulty never escalated this session)")
    for event in report.pushback_events:
        print(
            f"  turn {event.prior_turn_index}->{event.turn_index} "
            f"(difficulty {event.difficulty_from}->{event.difficulty_to}): "
            f"quality {event.quality_sum_prior}->{event.quality_sum_at} — {event.outcome.value}"
        )

    print("\nNarrative:")
    if report.narrative_fallback_used:
        print("  (narrative generation failed — numbers-only report)")
    else:
        print(f"  {report.narrative}")

    print("\nSuggestions:")
    if report.suggestions_fallback_used:
        print("  (suggestions unavailable this session)")
    elif not report.answer_suggestions:
        print("  (none)")
    else:
        for suggestion in report.answer_suggestions:
            print(f"  turn {suggestion.turn_index}: {suggestion.suggestion}")


if __name__ == "__main__":
    main()
