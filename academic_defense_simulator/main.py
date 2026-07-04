"""CLI orchestration for Academic Defense Simulator."""

from __future__ import annotations

from uuid import uuid4

from academic_defense_simulator.config import load_settings
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PANELIST_SYSTEM_PROMPT
from academic_defense_simulator.rag.chunking import chunk_pdf
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk, retrieve

_PANELIST_NAME = "Reyes"
_ACTIVE_ARCHETYPE = "methodology_expert"
_DIFFICULTY = 2


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


def main() -> None:
    settings = load_settings()

    # 1. PDF path
    pdf_path = input("PDF path: ").strip()

    # 2. Defense context
    defense_type, other_subtype = _prompt_defense_type()
    domain = input("Domain / discipline: ").strip()
    topic = input("Research title / topic: ").strip()

    # 3–4. Build profile
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

    # 7. Retrieval query = archetype's focus area
    archetype = ARCHETYPE_CONFIG[_ACTIVE_ARCHETYPE]
    query = archetype["archetype_focus"]

    # 8. Retrieve top chunk
    top_chunks = retrieve(query, chunks, top_k=1, embedding_model=embedding_model)
    if not top_chunks:
        raise RuntimeError("Retrieval returned no chunks — document may be empty after chunking.")
    retrieved_chunk = top_chunks[0]

    # 9. Fill prompt
    other_subtype_line = (
        f"\n- Defense subtype: {profile.other_subtype.value}"
        if profile.other_subtype is not None
        else ""
    )
    prompt = PANELIST_SYSTEM_PROMPT.format(
        panelist_name=_PANELIST_NAME,
        archetype_title=archetype["archetype_title"],
        archetype_focus=archetype["archetype_focus"],
        archetype_lane=archetype["archetype_lane"],
        defense_type=profile.defense_type.value,
        other_subtype_line=other_subtype_line,
        domain=profile.domain,
        topic=profile.topic,
        difficulty_level=_DIFFICULTY,
        retrieved_chunk=retrieved_chunk.text,
    )

    # 10. Generate question
    provider = GeminiProvider(api_key=settings.gemini_api_key)
    print(f"\nDr. {_PANELIST_NAME} is reviewing the document...\n")
    panelist_question: PanelistQuestion = provider.generate_structured(prompt, PanelistQuestion)

    # 11. Print question
    print(f"Dr. {_PANELIST_NAME}: {panelist_question.question}\n")
    print(f"[Grounding: \"{panelist_question.grounding_reference}\" — difficulty {panelist_question.difficulty_level}/5]\n")

    # 12. Capture answer
    input("Your answer: ")
    print("\nAnswer recorded. Session ended.")


if __name__ == "__main__":
    main()
