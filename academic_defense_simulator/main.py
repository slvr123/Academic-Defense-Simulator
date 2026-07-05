"""CLI orchestration for Academic Defense Simulator."""

from __future__ import annotations

import time
from uuid import uuid4

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import LLMProvider
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.prompts.panelist_prompts import (
    ARCHETYPE_CONFIG,
    FOLLOWUP_SYSTEM_PROMPT,
    PANELIST_SYSTEM_PROMPT,
    SCORING_SYSTEM_PROMPT,
)
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk, retrieve

_PANELIST_NAME = "Reyes"
_ACTIVE_ARCHETYPE = "methodology_expert"
MAX_TURNS = 6


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


def _clamp_difficulty(value: int) -> int:
    return max(1, min(5, value))


def _is_strong_answer(score: AnswerScore) -> bool:
    """Strong = no weak dimension (every quality axis >= 3) AND solidly high overall
    (clarity + depth + grounding >= 11 of 15). Deliberately not keyed on difficulty_delta:
    under the 'press on weakness' rubric a weak-but-engaged answer also escalates (+1), so
    only the sub-scores separate strong from weak. The all-axes-plus-sum test tolerates the
    model's per-axis noise (a genuinely strong answer may dip to 3 on one axis) without
    admitting a uniformly mediocre 3/3/3 or a fluent-but-ungrounded answer."""
    return (
        min(score.clarity, score.depth, score.grounding) >= 3
        and (score.clarity + score.depth + score.grounding) >= 11
    )


def _should_follow_up(previous_turn: ConversationTurn | None) -> bool:
    """Follow up only when the prior answer had a real weakness to press: a gap was named
    AND the answer was not strong. A strong answer advances to a new topic even if the
    scorer noted a residual gap — primary_gap stays honest for the v0.3 report, and
    branching no longer collapses to 'always follow up' now that the adversarial rubric
    surfaces a gap on nearly every answer."""
    if previous_turn is None or previous_turn.score is None:
        return False
    if previous_turn.score.primary_gap is None:
        return False
    return not _is_strong_answer(previous_turn.score)


def _generate_question(
    provider: LLMProvider,
    session: DefenseSession,
    chunks: list[Chunk],
    embedding_model: EmbeddingModel,
    archetype: dict[str, str],
    other_subtype_line: str,
) -> ConversationTurn:
    previous_turn = session.turns[-1] if session.turns else None
    is_followup = _should_follow_up(previous_turn)

    if not is_followup:
        query = archetype["archetype_focus"]
        results = retrieve(
            query,
            chunks,
            top_k=1,
            embedding_model=embedding_model,
            exclude_indices=frozenset(session.used_chunk_indices),
        )
        if not results:
            raise RuntimeError("Retrieval returned no chunks — document may be exhausted.")
        chunk_index, chunk = results[0]
        chunk_text = chunk.text
        prompt = PANELIST_SYSTEM_PROMPT.format(
            panelist_name=_PANELIST_NAME,
            archetype_title=archetype["archetype_title"],
            archetype_focus=archetype["archetype_focus"],
            archetype_lane=archetype["archetype_lane"],
            defense_type=session.profile.defense_type.value,
            other_subtype_line=other_subtype_line,
            domain=session.profile.domain,
            topic=session.profile.topic,
            difficulty_level=session.difficulty_current,
            retrieved_chunk=chunk_text,
        )
        print(f"[branch: new-topic — chunk {chunk_index}]")
    else:
        assert previous_turn is not None and previous_turn.score is not None
        chunk_index, chunk_text = previous_turn.chunk_index, previous_turn.chunk_text
        prompt = FOLLOWUP_SYSTEM_PROMPT.format(
            panelist_name=_PANELIST_NAME,
            archetype_title=archetype["archetype_title"],
            archetype_focus=archetype["archetype_focus"],
            archetype_lane=archetype["archetype_lane"],
            defense_type=session.profile.defense_type.value,
            previous_question=previous_turn.question,
            previous_answer=previous_turn.answer,
            primary_gap=previous_turn.score.primary_gap,
            retrieved_chunk=chunk_text,
            difficulty_level=session.difficulty_current,
        )
        print(f"[branch: follow-up — primary_gap: \"{previous_turn.score.primary_gap}\" — reusing chunk {chunk_index}]")

    panelist_question: PanelistQuestion = provider.generate_structured(prompt, PanelistQuestion)
    time.sleep(13)

    return ConversationTurn(
        question=panelist_question.question,
        grounding_reference=panelist_question.grounding_reference,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        difficulty_level=session.difficulty_current,
    )


def _score_answer(
    provider: LLMProvider,
    turn: ConversationTurn,
    archetype: dict[str, str],
    defense_type: str,
) -> AnswerScore:
    prompt = SCORING_SYSTEM_PROMPT.format(
        defense_type=defense_type,
        panelist_name=_PANELIST_NAME,
        archetype_title=archetype["archetype_title"],
        question=turn.question,
        answer=turn.answer,
        retrieved_chunk=turn.chunk_text,
    )
    return provider.generate_structured(prompt, AnswerScore)


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
    texts = chunk_pdf(pdf_path)
    if not texts:
        raise RuntimeError("No text extracted from the PDF — check the file path.")

    # 6. Embed chunks
    embedding_model = EmbeddingModel()
    embeddings = embedding_model.encode(texts)
    chunks = [Chunk(text=t, embedding=e) for t, e in zip(texts, embeddings)]

    other_subtype_line = (
        f"\n- Defense subtype: {profile.other_subtype.value}" if profile.other_subtype is not None else ""
    )

    archetype = ARCHETYPE_CONFIG[_ACTIVE_ARCHETYPE]
    print(f"[model: {settings.gemini_model}]")
    provider = GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
    session = DefenseSession(profile=profile, difficulty_current=profile.difficulty_start)

    for turn_num in range(1, MAX_TURNS + 1):
        print(f"\n=== Turn {turn_num}/{MAX_TURNS} (difficulty {session.difficulty_current}/5) ===")

        turn = _generate_question(provider, session, chunks, embedding_model, archetype, other_subtype_line)

        print(f"\nDr. {_PANELIST_NAME}: {turn.question}\n")
        print(f"[Grounding: \"{turn.grounding_reference}\" — difficulty {turn.difficulty_level}/5]\n")

        turn.answer = input("Your answer: ").strip()

        turn.score = _score_answer(provider, turn, archetype, profile.defense_type.value)
        print(
            f"[Score — clarity {turn.score.clarity}, depth {turn.score.depth}, "
            f"grounding {turn.score.grounding}, difficulty_delta {turn.score.difficulty_delta}, "
            f"primary_gap: {turn.score.primary_gap!r}]"
        )

        session.turns.append(turn)
        session.difficulty_current = _clamp_difficulty(session.difficulty_current + turn.score.difficulty_delta)

        if turn_num != MAX_TURNS:
            time.sleep(13)

    print("\nSession ended.")


if __name__ == "__main__":
    main()
