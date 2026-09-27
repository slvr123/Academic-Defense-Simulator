"""Boundary and provenance checks without a downloaded embedding model."""
from types import SimpleNamespace

import fitz
import pytest

from academic_defense_simulator.rag.chunking import SourcePassage, chunk_pages, extract_pages
from academic_defense_simulator.models.session import PersistedSession


class CharacterTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, **kwargs):
        return {"offset_mapping": [(i, i + 1) for i in range(len(text))]}

    def encode(self, text, **kwargs):
        return [0] * (len(text) + 2)


def test_long_paragraphs_fit_budget_preserve_text_and_page():
    model = SimpleNamespace(tokenizer=CharacterTokenizer(), max_seq_length=64)
    text = "abcdefghijklmnopqrstuvwxyz " * 30
    chunks = chunk_pages([SourcePassage(text, 7), SourcePassage("next page", 8)], model)
    assert all(len(model.tokenizer.encode(c.text)) <= 64 for c in chunks)
    first_page = [c.text for c in chunks if c.page_number == 7]
    restored = first_page[0] + "".join(c[32:] for c in first_page[1:])
    assert restored == text
    assert chunks[-1] == SourcePassage("next page", 8)


def test_pdf_page_numbers_include_blank_cover(tmp_path):
    path = tmp_path / "document.pdf"
    with fitz.open() as doc:
        doc.new_page()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 550, 750), "A synthetic research passage. " * 30)
        doc.save(path)
    pages = extract_pages(path)
    assert pages[0].text == ""
    assert pages[1].page_number == 2
    model = SimpleNamespace(tokenizer=CharacterTokenizer(), max_seq_length=64)
    assert all(c.page_number == 2 for c in chunk_pages(pages, model))


def test_legacy_session_has_no_invented_pages():
    from pathlib import Path
    raw = (Path(__file__).parent / "fixtures/legacy_session_v1.json").read_text()
    session = PersistedSession.model_validate_json(raw)
    assert session.document_chunk_pages == []
    assert session.session.turns[0].source_pages == []
    data = session.model_dump()
    data["document_chunk_pages"] = [[3]]
    with pytest.raises(ValueError, match="match the number"):
        PersistedSession.model_validate(data)
    data["document_chunk_pages"] = [[3], [4]]
    data["session"]["turns"][0]["source_pages"] = [3]
    restored = PersistedSession.model_validate(data)
    assert PersistedSession.model_validate_json(restored.model_dump_json()) == restored


def test_legacy_chunks_preserve_exact_page_provenance():
    from academic_defense_simulator.rag.chunking import _pack_with_sources
    chunks = _pack_with_sources([("a" * 2500, 1), ("b" * 200, 3), ("c" * 500, 4)])
    assert chunks[0].page_numbers == (1, 3)
    assert chunks[1].page_numbers == (3, 4)
    assert chunks[1].text.startswith("b" * 200)
