"""M9 L2: literature-review chunks are routed by panelist role, through the retriever
seam, composed with M6's near-tie sampling. L3: the note on a tagged chunk names the
researchers it cites."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from academic_defense_simulator.rag.retrieval import Chunk, retrieve
from backend import defense as defense_module
from backend.engine_adapter import ATTRIBUTION_NOTES, CITED_AUTHORS_NOTE, attribution_note, engine_text
from backend.page_filter import cited_authors
from backend.retrieval_sampling import NearTieRetriever, pick_near_tie
from backend.routing import LITERATURE_SPECIALIST_KEY, RoutedRetriever, blocked_chunks, routed
from backend.sessions import ChunkMeta
from backend.embedder import EMBEDDING_DIM
from backend.tests.conftest import CITED_MARKER, THESIS_WITH_REVIEW, make_page_pdf, make_settings, own_text, upload, wait_for

BARRED = [k for k in ARCHETYPE_CONFIG if k not in (LITERATURE_SPECIALIST_KEY, DEVILS_ADVOCATE_KEY)]

# Query [1, 0] against unit vectors [s, sqrt(1 - s^2)] scores s. Chunks 0 and 1 are
# tagged and the best two (a near-tie); 2 and 3 are the best untagged (a near-tie).
SCORES = [0.90, 0.89, 0.50, 0.495, 0.10]
ATTRIBUTIONS = ["literature_review", "partly_literature_review", None, None, None]
TAGGED, UNTAGGED_BAND = {0, 1}, {2, 3}


class QueryEncoder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def _chunks() -> list[Chunk]:
    return [Chunk(text=f"chunk {i}", embedding=[s, math.sqrt(1 - s * s)]) for i, s in enumerate(SCORES)]


def _metas(attributions=ATTRIBUTIONS) -> list[ChunkMeta]:
    return [ChunkMeta(i, f"chunk {i}", i, i, a) for i, a in enumerate(attributions)]


def _picks(archetype: str, access: str, seeds=range(200), exclude=frozenset()) -> set[int]:
    picks = set()
    for seed in seeds:
        retriever = routed(NearTieRetriever(seed), _metas(), archetype, access)
        [(index, _)] = retriever("q", _chunks(), top_k=1, embedding_model=QueryEncoder(), exclude_indices=exclude)
        picks.add(index)
    return picks


@pytest.mark.parametrize("access", ["literature", "none"])
@pytest.mark.parametrize("archetype", BARRED)
def test_a_barred_panelist_never_receives_a_tagged_chunk(archetype, access):
    assert _picks(archetype, access) == UNTAGGED_BAND  # both untagged near-ties, never 0 or 1


def test_the_literature_specialist_can_receive_a_tagged_chunk():
    assert _picks(LITERATURE_SPECIALIST_KEY, "literature") == TAGGED


def test_none_blocks_everyone_including_the_literature_specialist():
    assert _picks(LITERATURE_SPECIALIST_KEY, "none") == UNTAGGED_BAND
    for archetype in [*BARRED, LITERATURE_SPECIALIST_KEY]:
        assert blocked_chunks(_metas(), archetype, "none") == TAGGED


def test_routing_composes_with_near_tie_sampling():
    """The routed pick is the near-tie pick over the chunks left after both the
    engine's exclusions and the routing's: the band is formed among allowed chunks."""
    scores = np.array(SCORES)
    for seed in range(200):
        for exclude in (frozenset(), frozenset({2})):
            retriever = RoutedRetriever(NearTieRetriever(seed), frozenset(TAGGED))
            [(index, _)] = retriever("q", _chunks(), top_k=1, embedding_model=QueryEncoder(), exclude_indices=exclude)
            assert index == pick_near_tie(scores, exclude | TAGGED, seed)
    assert _picks("methodology_expert", "literature", exclude=frozenset({2})) == {3}


def test_routing_composes_with_v1_retrieve():
    retriever = RoutedRetriever(retrieve, frozenset(TAGGED))
    assert retriever("q", _chunks(), top_k=1, embedding_model=QueryEncoder())[0][0] == 2


def test_an_untagged_document_blocks_nothing_and_passes_every_call_through():
    calls = []

    def inner(query, chunks, top_k=1, *, embedding_model, exclude_indices=frozenset()):
        calls.append((query, chunks, top_k, embedding_model, exclude_indices))
        return [(0, chunks[0])]

    metas, chunks, encoder = _metas([None] * 5), _chunks(), QueryEncoder()
    for archetype in [*BARRED, LITERATURE_SPECIALIST_KEY]:
        for access in ("literature", "none"):
            assert blocked_chunks(metas, archetype, access) == frozenset()
            routed(inner, metas, archetype, access)("q", chunks, 1, embedding_model=encoder, exclude_indices=frozenset({3}))
            inner("q", chunks, 1, embedding_model=encoder, exclude_indices=frozenset({3}))
            assert calls[-2] == calls[-1]


# --- Through the API ------------------------------------------------------------------

LONG_THESIS = THESIS_WITH_REVIEW + [own_text(n) for n in range(4, 10)]  # three more untagged chunks
FULL_PANEL = ["methodology_expert", LITERATURE_SPECIALIST_KEY, "technical_implementation_reviewer"]


def _defense(client, pdf, panel: list[str]) -> tuple[list[dict], list[ChunkMeta]]:
    """Every question (panelist and source) from /start to the report, and the chunks."""
    session_id = upload(client, pdf, panel=json.dumps({"archetype_keys": panel})).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    start = client.post(f"/sessions/{session_id}/start")
    assert start.status_code == 200, start.json()
    questions = [start.json()["question"]]
    while True:
        body = client.post(f"/sessions/{session_id}/answers", json={"text": "We used thirty respondents."}).json()
        if body["done"]:
            return questions, client.app.state.store.get(session_id).chunks
        questions.append(body["next_question"])


@pytest.fixture
def literature_first(embedder, monkeypatch):
    """Every focus query and every chunk citing a study embed to the same vector, so
    without routing each panelist's best chunk is a tagged one."""
    focus = {config["archetype_focus"] for config in ARCHETYPE_CONFIG.values()}
    random_vector = embedder.encode_one

    def encode_one(text: str) -> np.ndarray:
        if text in focus or CITED_MARKER in text:
            return np.eye(1, EMBEDDING_DIM, dtype=np.float32)[0]
        return random_vector(text)

    monkeypatch.setattr(embedder, "encode_one", encode_one)


def _attribution(metas: list[ChunkMeta], question: dict):
    [meta] = [m for m in metas if m.text == question["sources"][0]["text"]]
    return meta.attribution


def test_in_a_defense_only_the_literature_specialist_draws_tagged_chunks(client_factory, tmp_path, literature_first):
    pdf = make_page_pdf(tmp_path / "Long Thesis.pdf", LONG_THESIS)
    with client_factory(lit_review_access="literature") as client:
        questions, metas = _defense(client, pdf, FULL_PANEL)
    assert sum(m.attribution is not None for m in metas) == 3 and sum(m.attribution is None for m in metas) == 4
    asked_by = {q["panelist"]["archetype_key"] for q in questions}
    assert set(FULL_PANEL) <= asked_by
    for question in questions:
        key = question["panelist"]["archetype_key"]
        if key not in (LITERATURE_SPECIALIST_KEY, DEVILS_ADVOCATE_KEY):
            assert _attribution(metas, question) is None, question["turn"]
    lit_turns = [q for q in questions if q["panelist"]["archetype_key"] == LITERATURE_SPECIALIST_KEY]
    assert all(_attribution(metas, q) is not None for q in lit_turns)  # its best chunks are all tagged


def test_in_a_defense_with_none_nobody_draws_tagged_chunks(client_factory, tmp_path, literature_first):
    pdf = make_page_pdf(tmp_path / "Long Thesis.pdf", LONG_THESIS)
    with client_factory(lit_review_access="none") as client:
        questions, metas = _defense(client, pdf, FULL_PANEL)
    # Devil's Advocate only contests a chunk someone else drew, so nothing is tagged.
    assert all(_attribution(metas, q) is None for q in questions)


def test_an_untagged_document_gives_the_engine_byte_identical_input(client_factory, tmp_path, gemini, monkeypatch):
    """The same defense with routing and without it (routed() as the identity, as
    before M9), with one fixed seed so two session ids can't differ: every prompt the
    engine sends, and every question and source the API returns, are the same."""
    pdf = make_page_pdf(tmp_path / "Untagged.pdf", [own_text(n) for n in range(1, 12)])
    monkeypatch.setattr(defense_module, "turn_seed", lambda _session, turn: turn)

    def run() -> tuple[list[str], list[dict]]:
        gemini.questions_asked, gemini.question_prompts = 0, []  # the fake numbers its questions
        with client_factory() as client:
            questions, metas = _defense(client, pdf, FULL_PANEL)
        assert all(m.attribution is None for m in metas)
        return gemini.question_prompts, questions

    routed_run = run()
    monkeypatch.setattr(defense_module, "routed", lambda inner, *_args: inner)
    assert run() == routed_run


def test_start_refuses_a_panel_the_untagged_chunks_cannot_seat(client_factory, tmp_path):
    pdf = make_page_pdf(tmp_path / "Kiosk Thesis.pdf", THESIS_WITH_REVIEW)  # one untagged chunk of four
    with client_factory(lit_review_access="literature") as client:
        session_id = upload(client, pdf, panel=json.dumps({"archetype_keys": ["methodology_expert", "technical_implementation_reviewer"]})).json()["session_id"]
        wait_for(client, session_id, lambda s: s.get("status") == "ready")
        response = client.post(f"/sessions/{session_id}/start")
    assert response.status_code == 422
    assert "1 passage(s) outside its literature review, too few for 2 panelists" in response.json()["detail"]


def test_lit_review_access_defaults_to_the_l5_verdict():
    assert make_settings().lit_review_access == "none"  # the judge: 5 of 16 counted as M


# --- L3: the note names the researchers ----------------------------------------------


def _meta(text: str, attribution="literature_review") -> ChunkMeta:
    return ChunkMeta(0, text, 0, 0, attribution)


def test_cited_authors_reads_every_citation_form():
    text = (
        "E. Reyes et al. (2025) used a CNN, as did Mendoza (2025). F. Garcia and R. Navarro (2021) and Aquino & Cho "
        "(2020) compared arrays (Bautista & Cruz, 2021a; Department of Health, 2024). Ono, Abe, Ito, and Sato "
        "(2019) found the same, Tan et al. agreed, and Reyes et al. later repeated it [12]. Locally, Villa and\nLim "
        "(2019) did too, as in Santos' (2003) model."
    )
    assert cited_authors(text) == [
        "Reyes et al. (2025)", "Mendoza (2025)", "Garcia and Navarro (2021)", "Aquino & Cho (2020)",
        "Bautista & Cruz (2021a)", "Department of Health (2024)", "Ono et al. (2019)", "Tan et al.",
        "Villa and Lim (2019)", "Santos (2003)",
    ]


def test_cited_authors_is_empty_without_a_named_citation():
    assert cited_authors("The system uses a microphone array [3, 7]. Chapter 2 (pages 4-9) reviews it.") == []


def test_a_tagged_chunk_that_names_researchers_lists_them_after_the_m8_note():
    meta = _meta("Reyes et al. (2025) built a CNN; Mendoza (2025) used a beamformer.")
    expected = (
        f"{ATTRIBUTION_NOTES['literature_review']}\n"
        "[Researchers cited in this excerpt: Reyes et al. (2025), Mendoza (2025). Their systems are not the "
        "candidate's. Ask how the candidate used, compared against or departed from them.]"
    )
    assert attribution_note(meta) == expected
    assert engine_text(meta, attribution_notes=True) == f"{expected}\n\n{meta.text}"
    assert CITED_AUTHORS_NOTE.startswith("[Researchers cited in this excerpt: ")


@pytest.mark.parametrize("attribution", ["literature_review", "partly_literature_review"])
def test_a_tagged_chunk_without_named_researchers_keeps_the_m8_note(attribution):
    meta = _meta("Earlier kiosks used a numbered scheme [4].", attribution)
    assert attribution_note(meta) == ATTRIBUTION_NOTES[attribution]
    assert engine_text(meta, attribution_notes=True) == f"{ATTRIBUTION_NOTES[attribution]}\n\n{meta.text}"


def test_the_author_list_needs_the_notes_flag_and_a_tag():
    cited = "Reyes et al. (2025) built a CNN."
    assert engine_text(_meta(cited), attribution_notes=False) == cited
    assert engine_text(_meta(cited, None), attribution_notes=True) == cited
