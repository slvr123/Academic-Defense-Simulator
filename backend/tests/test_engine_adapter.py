"""v1's real engine (`_generate_question`) driven through the adapter: chunks from a
session-style matrix, queries through the backend embedder, and the source each
question returns. The LLM is a stub; the embedder is the deterministic fake."""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np
import pytest

from academic_defense_simulator.engine import _generate_question, record_scored_turn, select_active_panelist
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.panel import FALLBACK_PANELISTS, apply_customizations, compose_full_roster
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from academic_defense_simulator.rag.retrieval import retrieve
from backend.engine_adapter import ATTRIBUTION_NOTES, RecordingEncoder, SourceTracker, chunk_copy_bytes, engine_chunks
from backend.schemas import PanelConfig
from backend.sessions import ChunkMeta
from backend.tests.conftest import FakeEmbedder

GROUNDING = "ISO/IEC 25010"  # in every chunk, so the stub's questions always pass is_grounded


class StubProvider:
    def generate_structured(self, prompt, response_model):
        assert response_model is PanelistQuestion
        return PanelistQuestion(question="Why that instrument?", grounding_reference=GROUNDING, difficulty_level=2)


class RecordingProvider(StubProvider):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_structured(self, prompt, response_model):
        self.prompts.append(prompt)
        return super().generate_structured(prompt, response_model)


def _document(embedder: FakeEmbedder, n: int = 8) -> tuple[np.ndarray, list[ChunkMeta]]:
    metas = [
        ChunkMeta(chunk_id=i, text=f"Section {i}. The proponents used {GROUNDING} for item {i}.",
                  page_start=2 * i, page_end=2 * i + 1)
        for i in range(n)
    ]
    return np.stack([embedder.encode_one(m.text) for m in metas]), metas


def _session(config: PanelConfig) -> DefenseSession:
    profile = config.to_profile(domain="Library science", topic="A library system", document_id="doc")
    panel = apply_customizations([FALLBACK_PANELISTS[k] for k in compose_full_roster(profile)], profile)
    return DefenseSession(profile=profile, panel=panel, difficulty_current=profile.difficulty_start)


def _score(strong: bool) -> AnswerScore:
    points = 5 if strong else 2
    return AnswerScore(
        clarity=points, depth=points, grounding=points, difficulty_delta=0,
        primary_gap="no evidence for the claim", answer_summary="The candidate claimed the tool is valid.",
    )


class Defense:
    """What a route will hold per defense: the engine inputs and the source tracker."""

    def __init__(self, config: PanelConfig) -> None:
        self.embedder = FakeEmbedder()
        self.matrix, self.metas = _document(self.embedder)
        self.chunks = engine_chunks(self.matrix, self.metas)
        self.encoder = RecordingEncoder(self.embedder)
        self.tracker = SourceTracker(self.matrix, self.metas)
        self.session = _session(config)

    def ask(self):
        panelist = select_active_panelist(self.session)
        turn = _generate_question(StubProvider(), self.session, self.chunks, self.encoder, panelist, "", "m")
        return turn, self.tracker.source_for(turn, self.encoder.take_query())

    def answer(self, turn, *, strong: bool) -> None:
        turn.answer = "Because it is the standard."
        turn.score = _score(strong)
        record_scored_turn(self.session, turn)

    def expected_best(self, archetype_key: str, exclude: set[int]) -> tuple[int, float]:
        query = self.embedder.encode_one(ARCHETYPE_CONFIG[archetype_key]["archetype_focus"])
        scores = self.matrix @ query
        best = max((i for i in range(len(self.metas)) if i not in exclude), key=lambda i: scores[i])
        return best, float(scores[best])


def test_new_topic_turns_retrieve_the_best_unused_chunk_with_its_score_and_pages():
    defense = Defense(PanelConfig(archetype_keys=["methodology_expert", "literature_theory_specialist"]))

    first, first_source = defense.ask()
    index, score = defense.expected_best("methodology_expert", exclude=set())
    assert first.chunk_index == index
    assert first_source.model_dump() == {
        "text": defense.metas[index].text, "page_start": 2 * index + 1, "page_end": 2 * index + 2,
        "score": pytest.approx(round(score, 4)), "origin": "new_topic",
    }

    defense.answer(first, strong=True)  # strong: the next panelist opens a new topic
    second, second_source = defense.ask()
    index2, score2 = defense.expected_best("literature_theory_specialist", exclude={index})
    assert second.panelist_archetype_key == "literature_theory_specialist"
    assert second.chunk_index == index2 != index  # retrieve's exclusion maps back to matrix rows
    assert second_source.origin == "new_topic" and second_source.score == pytest.approx(round(score2, 4))


def test_a_follow_up_reuses_the_chunk_and_its_first_score_without_embedding():
    defense = Defense(PanelConfig(archetype_keys=["methodology_expert", "literature_theory_specialist"]))
    first, first_source = defense.ask()
    defense.answer(first, strong=False)  # weak with a gap: the same panelist presses it

    calls_before = defense.embedder.calls
    follow_up, source = defense.ask()
    assert defense.embedder.calls == calls_before  # no query embedded
    assert follow_up.chunk_index == first.chunk_index
    assert source.origin == "follow_up"
    assert (source.score, source.page_start, source.text) == (first_source.score, first_source.page_start, first_source.text)


def test_devils_advocate_contests_an_earlier_chunk_with_that_chunks_score():
    defense = Defense(PanelConfig(archetype_keys=["methodology_expert"]))
    first, first_source = defense.ask()
    defense.answer(first, strong=True)  # every domain panelist has spoken: DA is next

    challenge, source = defense.ask()
    assert challenge.panelist_archetype_key == "devils_advocate"
    assert source.origin == "devils_advocate"
    assert challenge.chunk_index == first.chunk_index and source.score == first_source.score


def test_engine_chunks_follow_the_matrix_rows():
    embedder = FakeEmbedder()
    matrix, metas = _document(embedder, n=3)
    chunks = engine_chunks(matrix, metas)
    assert [c.text for c in chunks] == [m.text for m in metas]
    assert np.allclose(np.array([c.embedding for c in chunks]), matrix)
    with pytest.raises(ValueError, match="2 chunks for a matrix of 3 rows"):
        engine_chunks(matrix, metas[:2])


def test_recording_encoder_hands_over_the_query_once():
    encoder = RecordingEncoder(FakeEmbedder())
    [vector] = encoder.encode(["a query"])
    assert np.allclose(encoder.take_query(), vector)
    assert encoder.take_query() is None


# --- M7 J2/J3: attribution notes ------------------------------------------------


def _tagged_document(embedder: FakeEmbedder) -> tuple[np.ndarray, list[ChunkMeta]]:
    matrix, metas = _document(embedder, n=4)
    tags = [None, "literature_review", "partly_literature_review", None]
    return matrix, [replace(m, attribution=t) for m, t in zip(metas, tags)]


def test_with_notes_off_the_engine_sees_exactly_todays_text():
    matrix, metas = _tagged_document(FakeEmbedder())
    for chunks in (engine_chunks(matrix, metas), engine_chunks(matrix, metas, attribution_notes=False)):
        assert all(c.text is m.text for c, m in zip(chunks, metas))  # the same strings, not copies
    assert chunk_copy_bytes(engine_chunks(matrix, metas), metas) == chunk_copy_bytes(
        engine_chunks(matrix, [replace(m, attribution=None) for m in metas]), metas
    )


def test_with_notes_on_only_tagged_chunks_get_their_note():
    matrix, metas = _tagged_document(FakeEmbedder())
    chunks = engine_chunks(matrix, metas, attribution_notes=True)
    assert chunks[0].text is metas[0].text and chunks[3].text is metas[3].text
    assert chunks[1].text == f"{ATTRIBUTION_NOTES['literature_review']}\n\n{metas[1].text}"
    assert chunks[2].text == f"{ATTRIBUTION_NOTES['partly_literature_review']}\n\n{metas[2].text}"
    off = engine_chunks(matrix, metas)
    assert chunk_copy_bytes(chunks, metas) > chunk_copy_bytes(off, metas)  # the two annotated texts


def test_the_notes_name_no_page_numbers():
    assert set(ATTRIBUTION_NOTES) == {"literature_review", "partly_literature_review"}
    assert not any(re.search(r"\d", note) for note in ATTRIBUTION_NOTES.values())


def test_notes_cannot_move_retrieval():
    # The embeddings are the matrix rows either way, so retrieve ranks identically.
    embedder = FakeEmbedder()
    matrix, metas = _tagged_document(embedder)
    off, on = engine_chunks(matrix, metas), engine_chunks(matrix, metas, attribution_notes=True)
    assert [c.embedding for c in on] == [c.embedding for c in off]
    encoder = RecordingEncoder(embedder)
    for archetype in ARCHETYPE_CONFIG:
        query = ARCHETYPE_CONFIG[archetype]["archetype_focus"]
        ranked = [[i for i, _ in retrieve(query, chunks, top_k=4, embedding_model=encoder)] for chunks in (off, on)]
        assert ranked[0] == ranked[1]


def test_the_prompt_sees_the_note_and_the_source_reveal_does_not():
    embedder = FakeEmbedder()
    matrix, metas = _tagged_document(embedder)
    metas = [replace(m, attribution="literature_review") for m in metas]  # whichever chunk is picked
    chunks = engine_chunks(matrix, metas, attribution_notes=True)
    encoder, tracker = RecordingEncoder(embedder), SourceTracker(matrix, metas)
    session = _session(PanelConfig(archetype_keys=["methodology_expert"]))
    provider = RecordingProvider()
    turn = _generate_question(provider, session, chunks, encoder, select_active_panelist(session), "", "m")
    source = tracker.source_for(turn, encoder.take_query())
    note = ATTRIBUTION_NOTES["literature_review"]
    assert note in provider.prompts[0] and turn.chunk_text.startswith(note)
    assert source.text == metas[turn.chunk_index].text and note not in source.text
