from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_source_expander_shows_page_and_literal_passage():
    fixture = str(Path(__file__).parent / "fixtures/legacy_session_v1.json")
    app = AppTest.from_string(f'''
from pathlib import Path
from academic_defense_simulator.models.session import PersistedSession
from academic_defense_simulator.source_passage_ui import render_question_source
turn = PersistedSession.model_validate_json(Path({fixture!r}).read_text()).session.turns[0]
turn.source_pages = [9]
turn.chunk_text = "Literal <script> text from a document."
render_question_source(turn)
turn.source_pages = []
turn.grounding_flagged = True
render_question_source(turn)
''').run()
    assert not app.exception
    assert app.expander[0].label == "Source passage - PDF page 9"
    assert "<script>" in app.text[0].value
    assert any("not saved" in c.value for c in app.caption)
    assert "could not be verified" in app.warning[0].value
