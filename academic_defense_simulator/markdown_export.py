"""Human-readable Markdown transcript export (lean-docs, recorded 2026-08-01 in
`docs/v1.1a-archetype-expansion-decisions.md`). A second view over the same
`DefenseSession` the JSON export already serializes — never a source of truth,
never parsed back in. Pure function, no Streamlit import (standing import
boundary), zero I/O, deterministic for a fixed session/date.

`chunk_text` and `grounding_reference` are deliberately never rendered here —
same rule the main flow already applies (`streamlit_app._render_turn_content`).
`chunk_text` in particular is the field `v1.0.1 Decision 6` found leaking a
full document chunk into a committed fixture; this module structurally cannot
repeat that since it only reads `question`/`answer`/`score`/suggestion fields.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from academic_defense_simulator.models.report import AnswerSuggestion, DefenseReport
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from academic_defense_simulator.report import rescale_score_to_100

APP_NAME = "Academic Defense Simulator"
APP_URL = "https://academic-defense-simulator.streamlit.app/"


def _archetype_title(archetype_key: str) -> str:
    return ARCHETYPE_CONFIG[archetype_key]["archetype_title"]


def _title_case_enum_value(value: str) -> str:
    return value.replace("_", " ").title()


def _render_header(session: DefenseSession, prompt_version: str) -> str:
    profile = session.profile
    defense_type_line = _title_case_enum_value(profile.defense_type.value)
    if profile.other_subtype is not None:
        defense_type_line += f" / {_title_case_enum_value(profile.other_subtype.value)}"

    lines = [
        f"# {APP_NAME} — Session Transcript",
        "",
        f"**Document:** {profile.document_id}",
        f"**Defense type:** {defense_type_line}",
        f"**Domain / Topic:** {profile.domain} — {profile.topic}",
        f"**Exported:** {date.today().isoformat()}",
        f"**Turns:** {len(session.turns)}",
        f"**Prompt version:** {prompt_version}",
    ]
    return "\n".join(lines)


def _render_panel_roster(session: DefenseSession) -> str:
    lines = ["## Panel", ""]
    for panelist in session.panel:
        lines.append(
            f"- **Dr. {panelist.panelist_name}** — {_archetype_title(panelist.archetype_key)}: "
            f"{panelist.persona_framing}"
        )
    return "\n".join(lines)


def _score_line(turn: ConversationTurn) -> str:
    score = turn.score
    if score is None:
        return "**Score:** *(not scored)*"
    clarity = rescale_score_to_100(score.clarity)
    depth = rescale_score_to_100(score.depth)
    grounding = rescale_score_to_100(score.grounding)
    gap = score.primary_gap or "none noted"
    return f"**Score:** clarity {clarity}/100 · depth {depth}/100 · grounding {grounding}/100 · gap: {gap}"


def _suggestion_for_turn(turn_index: int, suggestions: list[AnswerSuggestion]) -> Optional[AnswerSuggestion]:
    for suggestion in suggestions:
        if suggestion.turn_index == turn_index:
            return suggestion
    return None


def _render_transcript(session: DefenseSession, report: Optional[DefenseReport]) -> str:
    suggestions = report.answer_suggestions if report is not None and not report.suggestions_fallback_used else []

    lines = ["## Transcript", ""]
    for i, turn in enumerate(session.turns):
        archetype_title = _archetype_title(turn.panelist_archetype_key)
        lines.append(f"### Turn {i + 1} — Dr. {turn.panelist_name} · {archetype_title}")
        lines.append("")
        lines.append(f"**Q:** {turn.question}")
        lines.append("")
        answer_text = turn.answer if turn.answer is not None else "*(unanswered)*"
        lines.append(f"**A:** {answer_text}")
        lines.append("")
        lines.append(_score_line(turn))

        suggestion = _suggestion_for_turn(i, suggestions)
        if suggestion is not None:
            lines.append("")
            lines.append(f"**Suggested improvement:** {suggestion.suggestion}")

        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_pushback_events(report: DefenseReport) -> str:
    if not report.pushback_events:
        return "*No escalation moments this session.*"
    lines = ["| Turn | Difficulty | Outcome |", "|---|---|---|"]
    for event in report.pushback_events:
        lines.append(
            f"| {event.turn_index + 1} | {event.difficulty_from} → {event.difficulty_to} | {event.outcome.value} |"
        )
    return "\n".join(lines)


def _render_report_section(report: Optional[DefenseReport]) -> str:
    lines = ["## Session Report", ""]

    if report is None:
        lines.append("*No report available — this session did not reach completion.*")
        return "\n".join(lines)

    if report.narrative_fallback_used or not report.narrative:
        lines.append("*Narrative unavailable for this session.*")
    else:
        lines.append(report.narrative)
    lines.append("")

    trajectory = " → ".join(str(d) for d in report.difficulty_trajectory)
    lines.append(f"**Difficulty trajectory:** {trajectory}")
    lines.append("")

    overall_clarity = rescale_score_to_100(report.overall_avg_clarity)
    overall_depth = rescale_score_to_100(report.overall_avg_depth)
    overall_grounding = rescale_score_to_100(report.overall_avg_grounding)
    lines.append(
        f"**Overall averages:** clarity {overall_clarity}/100 · depth {overall_depth}/100 · "
        f"grounding {overall_grounding}/100"
    )
    lines.append("")

    lines.append("### Per-panelist")
    lines.append("")
    for section in report.panelist_sections:
        archetype_title = _archetype_title(section.archetype_key)
        if section.turns_taken:
            clarity = rescale_score_to_100(section.avg_clarity)
            depth = rescale_score_to_100(section.avg_depth)
            grounding = rescale_score_to_100(section.avg_grounding)
            lines.append(
                f"- **Dr. {section.panelist_name}** — {archetype_title} · "
                f"{section.turns_taken} turn(s) — clarity {clarity}/100 · depth {depth}/100 · "
                f"grounding {grounding}/100"
            )
            for gap in section.primary_gaps:
                lines.append(f"  - {gap}")
        else:
            lines.append(f"- **Dr. {section.panelist_name}** — {archetype_title} · 0 turns")
    lines.append("")

    lines.append("### Pushback events")
    lines.append("")
    lines.append(_render_pushback_events(report))

    return "\n".join(lines)


def _render_footer() -> str:
    return f"---\n*{APP_NAME} — {APP_URL}*"


def render_session_markdown(session: DefenseSession, prompt_version: str) -> str:
    """Pure string-in/string-out render of a full session transcript. Never
    raises on a degraded session (aborted mid-session, unanswered final turn,
    missing suggestions, no pushback events) — every gap renders a sane line
    instead."""
    sections = [
        _render_header(session, prompt_version),
        _render_panel_roster(session),
        _render_transcript(session, session.report),
        _render_report_section(session.report),
        _render_footer(),
    ]
    return "\n\n".join(sections) + "\n"
