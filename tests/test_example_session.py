"""The recorded example session and the read-only stage that renders it
(v1.0.1 Decisions 5 and 6, brief steps 8-9, plus Decision 7's positioning copy).

Two things are under test here and they fail for different reasons.

The loader tests are ordinary business-logic tests: the committed fixture must
load, and each way it can be wrong must raise rather than half-render. The
redaction case is the one that matters — v1.0's close-out found a 3,827-character
verbatim document chunk inside a committed session JSON, and re-recording this
fixture is a routine operation, so the invariant needs a test and not a habit.

The copy tests are drift guards. `EXAMPLE_BANNER` and the Decision 7 strings are
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


def test_example_banner_is_the_locked_b3_copy() -> None:
    """Appendix B (B3), superseding Decision 5's original banner string."""
    assert app.EXAMPLE_BANNER == (
        "**Recorded example session — not live.**\n\n"
        "A complete defense against the sample capstone. The free demo is capped at "
        "four turns; use your own API key for a full session."
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


def test_positioning_copy_renders_above_the_mode_radio() -> None:
    """Decision 7's placement requirement. Bare mode gives no rendered output to
    inspect, so this asserts the call ordering in the intake block's source: the
    positioning copy must be emitted before `_render_key_gate`, which owns the
    mode radio and stops the script while it returns False."""
    source = inspect.getsource(app)
    intake_block = source.split('elif st.session_state.stage == "intake":', 1)[1]
    copy_at = intake_block.index("_render_positioning_copy()")
    gate_at = intake_block.index("if not _render_key_gate():")
    assert copy_at < gate_at


# --- the two entry points (Decision 5) ------------------------------------


def test_demo_exhausted_gate_offers_the_example_view() -> None:
    """Decision 5's launch-day failure case: both exhaustion paths funnel through
    `demo_disabled_reason`, and that branch must offer the recorded session
    rather than a disabled button and nothing else.

    Source-level rather than behavioural: the branch is reached only through
    Streamlit widget state, which bare mode cannot drive. It still pins the
    thing that matters — that the route out exists inside that branch."""
    gate_source = inspect.getsource(app._render_key_gate)
    exhausted_branch = gate_source.split("if demo_disabled_reason:", 1)[1]
    assert "goto_example_gate" in exhausted_branch
    assert 'st.session_state.stage = "example"' in exhausted_branch


def test_intake_offers_the_example_view_subordinately() -> None:
    """Decision 5 as amended: present on intake, but low-emphasis — a tertiary
    (link-styled) button, so it cannot compete with the upload affordance or the
    primary CTA."""
    source = inspect.getsource(app)
    assert 'key="goto_example_intake"' in source
    assert 'type="tertiary"' in source


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
