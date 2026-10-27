"""v2.0 M6 H3: `_generate_question`'s optional `retriever`. Its default must leave v1
exactly as it was; a supplied retriever replaces `retrieve` for new topics only."""

from __future__ import annotations

from academic_defense_simulator.engine import _generate_question
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import DefenseSession
from academic_defense_simulator.rag.retrieval import Chunk, retrieve

_PANELIST = Panelist(archetype_key="methodology_expert", panelist_name="Reyes", persona_framing="f", icon="🎓")
# Scores against the query [1, 0]: c0 0.6, c1 1.0, c2 0.0. retrieve's top-1 is c1.
_CHUNKS = [
    Chunk(text="chunk zero", embedding=[0.6, 0.8]),
    Chunk(text="chunk one", embedding=[1.0, 0.0]),
    Chunk(text="chunk two", embedding=[0.0, 1.0]),
]


class _QueryEncoder:
    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _Provider:
    def __init__(self):
        self.prompts = []

    def generate_structured(self, prompt, response_model):
        self.prompts.append(prompt)
        return PanelistQuestion(question="q", grounding_reference="chunk", difficulty_level=2)


def _session():
    profile = DefenseProfile(
        defense_type=DefenseType.THESIS, domain="d", topic="t", selected_archetypes=["methodology_expert"], document_id="doc"
    )
    return DefenseSession(profile=profile, panel=[_PANELIST], difficulty_current=2)


def _generate(**retriever_kwarg):
    provider = _Provider()
    turn = _generate_question(
        provider, _session(), _CHUNKS, _QueryEncoder(), _PANELIST, "", "gemini-3.1-flash-lite", **retriever_kwarg
    )
    return turn, provider.prompts


def test_the_default_is_v1s_retrieve_exactly():
    default_turn, default_prompts = _generate()
    assert default_turn.chunk_index == 1 and default_turn.chunk_text == "chunk one"
    for kwarg in ({"retriever": None}, {"retriever": retrieve}):
        turn, prompts = _generate(**kwarg)
        assert turn == default_turn
        assert prompts == default_prompts


def test_a_supplied_retriever_is_called_with_retrieves_arguments():
    calls = []

    def retriever(query, chunks, top_k=1, *, embedding_model, exclude_indices=frozenset()):
        calls.append((query, chunks, top_k, exclude_indices))
        return [(0, chunks[0])]

    turn, prompts = _generate(retriever=retriever)
    assert turn.chunk_index == 0 and turn.chunk_text == "chunk zero"
    assert "chunk zero" in prompts[0]
    [(query, chunks, top_k, excluded)] = calls
    assert chunks is _CHUNKS and top_k == 1 and excluded == frozenset()
    assert query.startswith("research design validity")  # the methodology archetype_focus
