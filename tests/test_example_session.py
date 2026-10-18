"""The recorded example session and the read-only stage that renders it
(v1.0.1 Decisions 5 and 6, brief steps 8-9, plus Decision 7's positioning copy).

Two things are under test here and they fail for different reasons.

The loader tests are ordinary business-logic tests: the committed fixture must
load, and each way it can be wrong must raise rather than half-render. The
redaction case is the one that matters — v1.0's close-out found a 3,827-character
verbatim document chunk inside a committed session JSON, and re-recording this
fixture is a routine operation, so the invariant needs a test and not a habit.

The copy tests are drift guards. The example banner and the Decision 7 strings are
locked wording that lives in the decisions doc; asserting them verbatim here
means a reworded first screen shows up as a failing test instead of shipping
quietly. They are deliberately literal — the point is to notice *any* change.

`streamlit_app` imports cleanly outside a running app ("bare mode"), the same
property `test_streamlit_persist.py` already relies on.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import academic_defense_simulator.streamlit_app as app
from academic_defense_simulator.example_session import (
    EXAMPLE_SESSION_PATH,
    ExampleSessionUnavailable,
    load_example_session,
)


# --- the committed fixture ------------------------------------------------


def test_committed_fixture_loads() -> None:
    persisted = load_example_session()
    assert persisted.session.turns, "fixture has no turns"
    assert persisted.session.report is not None


def test_committed_fixture_carries_no_document_text() -> None:
    """Decision 6's redaction requirement, asserted against the real committed
    file — not a constructed one. This is the check that would have caught the
    v1.0 leak."""
    persisted = load_example_session()
    assert persisted.document_chunks == []


def test_committed_fixture_has_no_duplicated_turn() -> None:
    """v1.3a Decision 3 tripwire. The previous recording carried one turn twice
    (identical question, answer and score) from the overlapping-rerun race; a
    re-recording that reintroduces that shape fails here instead of shipping."""
    turns = load_example_session().session.turns
    pairs = [(turn.question, turn.answer) for turn in turns]
    assert len(pairs) == len(set(pairs))


def test_missing_fixture_raises(tmp_path: Path) -> None:
    with pytest.raises(ExampleSessionUnavailable):
        load_example_session(tmp_path / "nope.json")


def test_unparseable_fixture_raises(tmp_path: Path) -> None:
    bad = tmp_path / "example_session.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExampleSessionUnavailable):
        load_example_session(bad)


def test_unredacted_fixture_is_rejected(tmp_path: Path) -> None:
    """The failure mode the invariant exists for: a fixture re-recorded and
    committed without running the redaction. Built by taking the real committed
    file and putting document text back, so this exercises the exact shape a
    genuine mistake would produce."""
    raw = json.loads(EXAMPLE_SESSION_PATH.read_text(encoding="utf-8"))
    raw["document_chunks"] = ["a verbatim passage of the source document"]
    leaky = tmp_path / "example_session.json"
    leaky.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ExampleSessionUnavailable, match="unredacted"):
        load_example_session(leaky)


def test_fixture_without_report_is_rejected(tmp_path: Path) -> None:
    raw = json.loads(EXAMPLE_SESSION_PATH.read_text(encoding="utf-8"))
    raw["session"]["report"] = None
    no_report = tmp_path / "example_session.json"
    no_report.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ExampleSessionUnavailable, match="no report"):
        load_example_session(no_report)


# --- locked copy ----------------------------------------------------------


def test_example_banner_omits_the_cap_sentence_when_the_demo_covers_the_recording() -> None:
    """v1.3a Decision 2 (supersedes v1.0.1 B3): at the deployed cap of 12 the
    8-turn recording is within reach of the demo, so no cap sentence."""
    assert app.example_banner(recorded_turns=8, demo_turns=12) == (
        "**Recorded example session — not live.**" + chr(10) * 2
        + "A complete defense against the sample capstone."
    )


def test_example_banner_names_the_live_cap_when_it_is_shorter_than_the_recording() -> None:
    assert app.example_banner(recorded_turns=8, demo_turns=4) == (
        "**Recorded example session — not live.**" + chr(10) * 2
        + "A complete defense against the sample capstone. The free demo is capped at "
        "4 turns; use your own API key for a full session."
    )


def test_positioning_headline_is_locked_decision_7_copy() -> None:
    assert app.POSITIONING_HEADLINE == (
        "Most RAG demos answer questions about your document. This one asks them."
    )


def test_positioning_expander_title_is_locked() -> None:
    assert app.POSITIONING_EXPANDER_TITLE == "How this works"


def test_positioning_bullets_are_locked_copy() -> None:
    """Reordered 2026-07-29 (Sean): reverse-RAG framing leads, retention bullet
    moved last. That bullet still says "processed transiently" rather than "in
    memory" — the upload does touch disk as a temp file for the length of the
    chunking pass, so "in memory" would be the same kind of not-quite-true claim
    this wording exists to avoid."""
    assert app.POSITIONING_BULLETS == (
        "This uses retrieval-augmented generation in reverse. Most RAG systems "
        "retrieve passages to answer questions - here, each panelist retrieves a "
        "passage from your document and uses it to ask one. Questions are "
        "grounded in your specific text, not the topic in general.",
        "Your answer is scored behind the scenes, and that score steers how hard the "
        "next question is. You never see the score during the session.",
        "Your document is chunked and embedded locally, processed transiently, and "
        "never retained. Retrieved passages are sent to a hosted language model to "
        "generate each question; nothing else is stored or reused.",
    )


def _intake_block_source() -> str:
    source = inspect.getsource(app)
    return source.split('elif st.session_state.stage == "intake":', 1)[1].split(
        'elif st.session_state.stage == "running":', 1
    )[0]


def test_intake_renders_in_the_locked_order() -> None:
    """v1.3a Decision 2's order, with v1.0.1 Decision 7's copy still first:
    positioning copy, recorded defense, mode line, then the upload step. Bare mode
    gives no rendered output to inspect, so this asserts call order in source."""
    intake = _intake_block_source()
    order = [
        intake.index("_render_positioning_copy()"),
        intake.index("_render_example_entry("),
        intake.index("_render_mode_line()"),
        intake.index("st.file_uploader("),
    ]
    assert order == sorted(order)


def test_exhausted_state_promotes_the_example_and_disables_the_upload_step() -> None:
    intake = _intake_block_source()
    assert "_render_example_entry(primary=exhausted_reason is not None)" in intake
    assert intake.count("disabled=exhausted_reason is not None") == 2  # uploader, sample
    assert "uploaded_file is None or exhausted_reason is not None" in intake  # Process
    entry = inspect.getsource(app._render_example_entry)
    assert 'type="primary" if primary else "secondary"' in entry


def test_slot_is_spent_before_any_llm_call_on_both_document_paths() -> None:
    """The grant check sits ahead of the upload path's relevance gate and the
    sample path's extraction."""
    intake = _intake_block_source()
    grant_at = intake.index("not _ensure_session_key()")
    assert grant_at < intake.index("_ingest_and_extract(uploaded_file)")
    assert grant_at < intake.index("_load_sample_and_extract()")


def test_grant_follows_the_pure_plan_under_the_session_lock() -> None:
    """The write order and its stop-point safety live in
    `demo_counter.plan_demo_grant` (tested per stop point in test_demo_counter.py);
    this pins that the app derives its writes from that plan, under the lock, and
    spends the daily counter in exactly one place."""
    source = inspect.getsource(app._ensure_session_key)
    lock_at = source.index("with st.session_state.turn_lock:")
    body = source[lock_at:]
    assert body.index("plan_demo_grant(") > 0
    assert body.count("consume_demo_session()") == 1


def test_example_entry_is_hidden_once_a_document_is_loaded() -> None:
    """D2 refinement (2026-10-18): landing and exhausted states only."""
    intake = _intake_block_source()
    guard_at = intake.index('if "document_id" not in st.session_state:')
    assert guard_at < intake.index("_render_example_entry(") < guard_at + 300


def test_example_entry_says_read_not_watch() -> None:
    """2026-10-18: "Watch" promised a video; the page is a transcript."""
    entry = inspect.getsource(app._render_example_entry)
    assert '"Read a recorded defense"' in entry
    assert "Watch a recorded defense" not in inspect.getsource(app)


def test_example_stage_opens_only_the_first_turn() -> None:
    stage = inspect.getsource(app._render_example_stage)
    assert "_render_exchange_history(session, expand_first=True)" in stage
    history = inspect.getsource(app._render_exchange_history)
    assert "expanded=expand_first and i == 1" in history


def test_recorded_example_path_never_touches_the_demo_counter() -> None:
    for function in (app._render_example_entry, app._render_example_stage):
        assert "consume_demo_session" not in inspect.getsource(function)


def test_example_stage_has_no_answer_box_or_dev_toggle() -> None:
    """"Read-only" in Decision 5 is a list of absences, so they are what gets
    asserted. Matched against `st.`-qualified call sites, not bare words — the
    function's own docstring names the controls it deliberately omits, and a
    plain substring check scores that as a violation."""
    stage_source = inspect.getsource(app._render_example_stage)
    for forbidden in ("st.text_area(", "st.chat_input(", "st.toggle(", "st.form_submit_button("):
        assert forbidden not in stage_source, f"example stage renders {forbidden}"


def test_example_stage_suppresses_the_sidebar_renderers() -> None:
    source = inspect.getsource(app)
    assert 'if st.session_state.stage != "example":' in source


# --- import boundary ------------------------------------------------------


def test_example_session_module_is_streamlit_free() -> None:
    """Standing architecture rule: business-logic modules never import Streamlit.
    Asserted here as well as by the `git grep` in the session evidence, because
    a test is what actually holds the line on the next edit.

    Parsed rather than grepped: the module's docstring says the words "no
    `streamlit` import", and a substring check reads its own compliance note as
    a breach."""
    import ast

    import academic_defense_simulator.example_session as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(name.split(".")[0] == "streamlit" for name in imported), imported
