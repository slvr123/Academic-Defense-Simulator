"""Structured cross-panelist digest — render-only, zero LLM calls.

Gives every panelist visibility into what prior panelists asked and what the candidate
claimed, without the Pattern-1 dilution risk of a full transcript. See
`docs/v0.3b-multi-panelist-orchestration-decisions.md` Decision 1 for why a structured
digest was chosen over a full transcript or a rolling summary.

Digest schema (locked, per prior turn): panelist_id, persona_name, question (verbatim),
answer_summary, primary_gap, score. Rendered chronologically from session state into a
prompt-ready block. This module never calls an LLM.
"""

from __future__ import annotations

from academic_defense_simulator.models.session import ConversationTurn, DefenseSession

# Cap on how many prior turns are rendered into a panelist's context. N is an
# implementation decision (not a design decision — see Decision 1); 6 keeps the block
# bounded even in a long session without adaptive logic.
DIGEST_MAX_TURNS = 6

_DIGEST_HEADER = (
    "Context from earlier in this session (other panelists' exchanges — for awareness "
    "only; stay in your own lane, do not repeat a question already asked):\n"
)


def turn_total_score(turn: ConversationTurn) -> int:
    """Sum of the three quality sub-scores (max 15) — the digest's single 'score' field,
    and the ranking key Devil's Advocate uses to pick the strongest prior claim."""
    assert turn.score is not None
    return turn.score.clarity + turn.score.depth + turn.score.grounding


def _render_entry(turn: ConversationTurn, index: int) -> str:
    assert turn.score is not None
    return (
        f"{index}. panelist_id: {turn.panelist_archetype_key} (Dr. {turn.panelist_name})\n"
        f"   question: {turn.question}\n"
        f"   answer_summary: {turn.score.answer_summary}\n"
        f"   primary_gap: {turn.score.primary_gap}\n"
        f"   score: {turn_total_score(turn)}/15\n"
    )


def render_digest_block(session: DefenseSession, max_turns: int = DIGEST_MAX_TURNS) -> str:
    """Chronological, structured digest of the last `max_turns` scored turns, as a
    ready-to-inject prompt block (including its own header). Empty string when no prior
    turn has been scored yet (turn 1) — templates treat this as an empty conditional
    section, same pattern as `previous_answer_line`/`other_subtype_line`."""
    scored_turns = [t for t in session.turns if t.score is not None]
    if not scored_turns:
        return ""
    windowed = scored_turns[-max_turns:]
    entries = "\n".join(_render_entry(t, i) for i, t in enumerate(windowed, start=1))
    return _DIGEST_HEADER + entries
