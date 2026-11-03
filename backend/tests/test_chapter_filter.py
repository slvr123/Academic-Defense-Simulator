"""M14: chapter detection at ingest (D1) and the opening-question chapter filter (D2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from academic_defense_simulator.models.session import ConversationTurn
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.rag.retrieval import Chunk
import backend.defense as defense_module
from backend.chapter_filter import ChapterRetriever, is_opening, off_chapter, target_chapter
from backend.defense import Defense, _opening_filter
from backend.embedder import EMBEDDING_DIM
from backend.page_filter import chapter_heading_number, chapter_of, chapter_pdf_map, chapter_starts
from backend.retrieval_sampling import NearTieRetriever
from backend.routing import LITERATURE_SPECIALIST_KEY, RoutedRetriever, routed
from backend.sessions import ChunkMeta
from backend.tests.conftest import cited_text, make_page_pdf, make_settings, own_text, upload, wait_for

REPO = Path(__file__).resolve().parents[2]
ANICHECK = REPO / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf"
SAMPLE3 = REPO / "sample3.pdf"

FILLER = "The system was tested with the staff of the library over two weeks of normal use."


def text_page(heading: str = "", lines: int = 8) -> str:
    return "\n".join(([heading] if heading else []) + [FILLER] * lines)


# --- D1: detection -------------------------------------------------------------------


def one_based(starts: dict[int, int] | None) -> dict[int, int] | None:
    return None if starts is None else {n: page + 1 for n, page in sorted(starts.items())}


def test_the_anicheck_map_is_m12s():
    assert one_based(chapter_pdf_map(ANICHECK)) == {1: 4, 2: 6, 3: 8, 4: 11, 5: 14}


@pytest.mark.skipif(not SAMPLE3.exists(), reason="sample3.pdf is not in this checkout")
def test_the_sample3_map_is_m12s():
    assert one_based(chapter_pdf_map(SAMPLE3)) == {1: 5, 2: 13, 3: 43}


@pytest.mark.parametrize(
    "line, number",
    [
        ("CHAPTER 1", 1),
        ("CHAPTER 2: REVIEW OF RELATED LITERATURE", 2),
        ("Chapter III - Methodology", 3),
        ("CHAPTER FOUR", 4),
        ("Chapter 5.", 5),
        ("CHAPTER 3 METHODOLOGY", 3),
        ("Chapter 3 describes the methodology", None),  # prose
        ("CHAPTER 3: METHODOLOGY ........ 43", None),  # a contents line
        ("CHAPTER 3: METHODOLOGY 43", None),
        ("The chapter 1 results", None),
    ],
)
def test_a_heading_is_a_heading_and_prose_is_not(line, number):
    assert chapter_heading_number(line) == number


def test_a_toc_page_and_a_page_with_several_headings_are_ignored():
    toc = "\n".join(["TABLE OF CONTENTS"] + [f"CHAPTER {n}: TITLE {'.' * 10} {n * 4}" for n in range(1, 6)])
    two_headings = text_page("CHAPTER 1") + "\nCHAPTER 2\n" + FILLER
    pages = [toc, two_headings, text_page("CHAPTER 1"), text_page("CHAPTER 2"), text_page("CHAPTER 3")]
    assert chapter_starts(pages) == {1: 2, 2: 3, 3: 4}


def test_prose_naming_a_chapter_starts_nothing_and_only_the_first_page_of_a_number_counts():
    pages = [
        text_page("CHAPTER 1"),
        text_page("Chapter 3 describes how the system was evaluated."),
        text_page("CHAPTER 2"),
        text_page("CHAPTER 3"),
        text_page("CHAPTER 2"),  # a repeated running header: the first page stands
    ]
    assert chapter_starts(pages) == {1: 0, 2: 2, 3: 3}


def test_pages_before_chapter_one_are_chapter_zero():
    starts = {1: 2, 2: 4, 3: 6}
    assert [chapter_of(page, starts) for page in range(8)] == [0, 0, 1, 1, 2, 2, 3, 3]


@pytest.mark.parametrize(
    "headings",
    [
        ["CHAPTER 1", "CHAPTER 2"],  # fewer than 3 chapters
        ["CHAPTER 2", "CHAPTER 3", "CHAPTER 4"],  # no chapter 1
        ["CHAPTER 1", "CHAPTER 3", "CHAPTER 2"],  # starts don't increase with the number
    ],
)
def test_an_untrustworthy_map_is_none(headings):
    assert chapter_starts([text_page(h) for h in headings]) is None


# --- D2: the pure functions ----------------------------------------------------------


def turn(key: str) -> ConversationTurn:
    return ConversationTurn(
        panelist_archetype_key=key, panelist_name="x", question="?", grounding_reference="",
        chunk_index=0, chunk_text="", difficulty_level=2,
    )


def test_only_a_panelist_who_has_not_asked_is_opening():
    turns = [turn("problem_objectives_reviewer")]
    assert not is_opening(turns, "problem_objectives_reviewer")
    assert is_opening(turns, "methodology_expert")


def test_the_target_is_the_home_chapter_clamped_to_the_last_chapter():
    five = {1: 0, 2: 2, 3: 4, 4: 6, 5: 8}
    three = {1: 0, 2: 2, 3: 4}
    assert target_chapter("problem_objectives_reviewer", five) == 1
    assert target_chapter("results_conclusions_reviewer", five) == 4
    assert target_chapter("results_conclusions_reviewer", three) == 3  # sample3's case
    assert target_chapter("ethics_practicality_reviewer", three) == 3


def metas(pages: list[int], tagged: frozenset[int] = frozenset()) -> list[ChunkMeta]:
    return [
        ChunkMeta(chunk_id=i, text=f"chunk {i}", page_start=p, page_end=p,
                  attribution="literature_review" if i in tagged else None)
        for i, p in enumerate(pages)
    ]


STARTS = {1: 1, 2: 3, 3: 5}
PAGES = [0, 1, 2, 3, 4, 5, 6]  # chapters 0, 1, 1, 2, 2, 3, 3


def test_off_chapter_is_every_chunk_outside_the_target():
    assert off_chapter(metas(PAGES), 1, STARTS) == frozenset({0, 3, 4, 5, 6})
    assert off_chapter(metas(PAGES), 3, STARTS) == frozenset({0, 1, 2, 3, 4})


class Encoder:
    def encode(self, texts):
        return [np.eye(1, EMBEDDING_DIM)[0].tolist() for _ in texts]


def chunks(n: int) -> list[Chunk]:
    # Chunk 0 scores best, then 1, then 2...: the argmax of what's left is its lowest index.
    return [Chunk(text=f"chunk {i}", embedding=(np.eye(1, EMBEDDING_DIM)[0] * (1 - i / 10)).tolist()) for i in range(n)]


def recording_inner(calls: list[frozenset[int]]):
    def inner(query, chunk_list, top_k=1, *, embedding_model, exclude_indices=frozenset()):
        calls.append(exclude_indices)
        return NearTieRetriever(seed=1)(query, chunk_list, top_k, embedding_model=embedding_model,
                                        exclude_indices=exclude_indices)
    return inner


def test_the_filter_narrows_and_reports_applied():
    calls: list[frozenset[int]] = []
    retriever = ChapterRetriever(recording_inner(calls), off_chapter(metas(PAGES), 2, STARTS))
    [(index, _)] = retriever("q", chunks(7), 1, embedding_model=Encoder(), exclude_indices=frozenset({3}))
    assert index == 4 and retriever.outcome == "applied"
    assert calls == [frozenset({0, 1, 2, 3, 5, 6})]


def test_the_fallback_drops_only_the_chapter_exclusion_and_m9_still_blocks():
    """The Literature Specialist under lit_review_access=none: its Chapter 2 (chunks 3 and
    4) is all literature review, so routing blocks it, the filter leaves nothing, and the
    fallback retrieves with routing and the used chunks still excluded."""
    chapter_metas = metas(PAGES, tagged=frozenset({3, 4}))
    calls: list[frozenset[int]] = []
    routed_inner = routed(recording_inner(calls), chapter_metas, LITERATURE_SPECIALIST_KEY, "none")
    retriever = ChapterRetriever(routed_inner, off_chapter(chapter_metas, 2, STARTS))
    [(index, _)] = retriever("q", chunks(7), 1, embedding_model=Encoder(), exclude_indices=frozenset({0}))
    assert retriever.outcome == "fallback_no_candidates"
    assert calls[0] == frozenset(range(7))  # the filtered try: everything excluded
    assert calls[1] == frozenset({0, 3, 4})  # the fallback: used chunk plus M9's blocks
    assert index == 1 and index not in {3, 4}


# --- D2: wired into the defense ------------------------------------------------------


def defense_for(pages: list[int], starts, flag: bool, turns: list[ConversationTurn] = ()) -> tuple[Defense, SimpleNamespace]:
    session = SimpleNamespace(turns=list(turns))
    defense = Defense(
        session=None, chunks=[], encoder=None, tracker=SimpleNamespace(chunks=metas(PAGES)),  # type: ignore[arg-type]
        providers=None, calls=None, model="m", other_subtype_line="",  # type: ignore[arg-type]
        opening_chapter_filter=flag, chapter_starts=starts,
    )
    return defense, session


def test_the_filter_applies_to_a_first_question_only():
    inner = RoutedRetriever(lambda *a, **k: [], frozenset())
    defense, session = defense_for(PAGES, STARTS, True)
    opening = _opening_filter(defense, session, "problem_objectives_reviewer", inner)  # type: ignore[arg-type]
    assert isinstance(opening, ChapterRetriever) and opening.inner is inner
    assert opening.off_chapter == frozenset({0, 3, 4, 5, 6})
    # A panelist who has asked (its follow-ups, and any later new topic of its own): untouched.
    defense, session = defense_for(PAGES, STARTS, True, [turn("problem_objectives_reviewer")])
    assert _opening_filter(defense, session, "problem_objectives_reviewer", inner) is None  # type: ignore[arg-type]
    # The Devil's Advocate never is.
    defense, session = defense_for(PAGES, STARTS, True)
    assert _opening_filter(defense, session, DEVILS_ADVOCATE_KEY, inner) is None  # type: ignore[arg-type]


def test_no_chapter_map_or_the_flag_off_leaves_the_retriever_as_before():
    inner = RoutedRetriever(lambda *a, **k: [], frozenset())
    for starts, flag in ((None, True), (STARTS, False), (None, False)):
        defense, session = defense_for(PAGES, starts, flag)
        assert _opening_filter(defense, session, "problem_objectives_reviewer", inner) is None  # type: ignore[arg-type]


def test_the_flag_defaults_on_by_e2s_verdict():
    assert make_settings().opening_chapter_filter is True  # 10 of 10 rounds, M14 E2


# --- Through the API ------------------------------------------------------------------

# Five chapters of four pages each, so v1's two-page chunks never straddle a chapter;
# Chapter 2 is the literature review (tagged by M7, blocked by M9 under "none").
def _chapter(heading: str, text) -> list[str]:
    return [f"{heading}\n{text(0)}"] + [text(n) for n in range(1, 4)]


CHAPTERED = (
    _chapter("CHAPTER 1: THE PROBLEM AND ITS BACKGROUND", lambda n: own_text(10 + n))
    + _chapter("CHAPTER 2: REVIEW OF RELATED LITERATURE", lambda n: cited_text(20 + n))
    + _chapter("CHAPTER 3: METHODOLOGY", lambda n: own_text(30 + n))
    + _chapter("CHAPTER 4: RESULTS AND DISCUSSION", lambda n: own_text(40 + n))
    + _chapter("CHAPTER 5: CONCLUSIONS AND RECOMMENDATIONS", lambda n: own_text(50 + n))
)
THESIS_PANEL = ["methodology_expert", "problem_objectives_reviewer", "results_conclusions_reviewer"]


def run_defense(client, pdf: Path, panel: list[str]) -> tuple[list[dict], object]:
    session_id = upload(client, pdf, panel=json.dumps({"archetype_keys": panel})).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    start = client.post(f"/sessions/{session_id}/start")
    assert start.status_code == 200, start.json()
    questions = [start.json()["question"]]
    while True:
        body = client.post(f"/sessions/{session_id}/answers", json={"text": "We used thirty respondents."}).json()
        if body["done"]:
            return questions, client.app.state.store.get(session_id)
        questions.append(body["next_question"])


@pytest.fixture
def chaptered_pdf(tmp_path) -> Path:
    return make_page_pdf(tmp_path / "Chaptered Thesis.pdf", CHAPTERED)


def test_ingest_stores_the_map_and_each_opener_draws_from_its_chapter(client_factory, chaptered_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.defense")
    with client_factory(opening_chapter_filter=True) as client:
        questions, session = run_defense(client, chaptered_pdf, THESIS_PANEL)
    starts = session.chapter_starts
    assert one_based(starts) == {1: 1, 2: 5, 3: 9, 4: 13, 5: 17}
    seen: set[str] = set()
    for question in questions:
        key, source = question["panelist"]["archetype_key"], question["sources"][0]
        if key != DEVILS_ADVOCATE_KEY and key not in seen:
            seen.add(key)
            assert chapter_of(source["page_start"] - 1, starts) == target_chapter(key, starts), question["turn"]
    assert seen == set(THESIS_PANEL)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("opening question")]
    assert len(lines) == 3 and all("chapter_filter=applied" in line for line in lines)
    assert all("text" not in line for line in lines)  # pages only


def test_the_literature_specialist_falls_back_and_m9_still_blocks(client_factory, chaptered_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.defense")
    panel = [LITERATURE_SPECIALIST_KEY, "methodology_expert", "problem_objectives_reviewer"]
    with client_factory(opening_chapter_filter=True, lit_review_access="none") as client:
        questions, session = run_defense(client, chaptered_pdf, panel)
    by_text = {m.text: m for m in session.chunks}
    assert all(by_text[q["sources"][0]["text"]].attribution is None for q in questions)  # nothing tagged, ever
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("opening question")]
    lit = [line for line in lines if LITERATURE_SPECIALIST_KEY in line]
    assert len(lit) == 1 and "chapter_filter=fallback_no_candidates" in lit[0]
    assert all("chapter_filter=applied" in line for line in lines if LITERATURE_SPECIALIST_KEY not in line)


def test_flag_off_and_no_chapter_map_give_the_engine_identical_input(client_factory, chaptered_pdf, gemini, monkeypatch):
    """Same seed, three runs: the flag off; the flag on with no chapter map; and the
    retriever exactly as M12 built it (no M14 code in the path). Every prompt, question
    and source is the same."""
    monkeypatch.setattr(defense_module, "turn_seed", lambda _session, turn: turn)

    def run(**settings) -> tuple[list[str], list[dict]]:
        gemini.questions_asked, gemini.question_prompts = 0, []
        with client_factory(**settings) as client:
            questions, _session = run_defense(client, chaptered_pdf, THESIS_PANEL)
        return gemini.question_prompts, questions

    flag_off = run(opening_chapter_filter=False)
    monkeypatch.setattr("backend.ingest_queue.IngestResult.__init__", _without_map(monkeypatch))
    assert run(opening_chapter_filter=True) == flag_off
    monkeypatch.setattr(defense_module, "_opening_filter", lambda *_args: None)
    assert run(opening_chapter_filter=True) == flag_off


def _without_map(monkeypatch):
    """IngestResult's constructor with the chapter map dropped, as for a PDF without headings."""
    from backend.ingest_queue import IngestResult

    original = IngestResult.__init__

    def init(self, *args, **kwargs):
        kwargs["chapter_starts"] = None
        original(self, *args, **kwargs)

    return init

