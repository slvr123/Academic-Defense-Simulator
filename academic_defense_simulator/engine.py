"""Turn-loop engine — orchestration logic shared by every driver (CLI, Streamlit, and
any future FastAPI wrapper). No `streamlit` import anywhere in this module or anything
it imports: that is the import-boundary contract (standing constraint 4) and the
bisection point the parked Streamlit-hang investigation needs (v0.3b Decision 6).

Drivers own I/O (prompting for input, rendering output, sleeping between reruns) and
call into this module for: which panelist speaks next (`select_active_panelist`),
question generation (`_generate_question`, dispatching internally to the Devil's
Advocate path when appropriate), scoring (`_score_answer`), and difficulty clamping.
"""

from __future__ import annotations

import logging
import time

from academic_defense_simulator.digest import render_digest_block, turn_total_score
from academic_defense_simulator.grounding import is_grounded
from academic_defense_simulator.llm.provider import LLMProvider
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.prompts.panelist_prompts import (
    ACKNOWLEDGMENT_INSTRUCTION,
    ARCHETYPE_CONFIG,
    DEVILS_ADVOCATE_SYSTEM_PROMPT,
    FOLLOWUP_SYSTEM_PROMPT,
    HIGH_DIFFICULTY_GROUNDING_GUARD,
    PANELIST_SYSTEM_PROMPT,
    PREVIOUS_ANSWER_LINE,
    SCORING_SYSTEM_PROMPT,
)
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk, retrieve

logger = logging.getLogger(__name__)

MAX_BLANK_ATTEMPTS = 3
MAX_FOLLOW_UPS_PER_TOPIC = 2  # hard cap: after this many follow-ups on one chunk, force a new topic

# Quality-sum (clarity + depth + grounding, max 15) floor for a "strong" answer. Named so
# report.py's pushback classification (v0.3c Decision 1) can reuse it instead of
# re-declaring the literal — same threshold shaping both the branching logic here and the
# "recovered" outcome in the end-of-session report.
STRONG_ANSWER_SUM_THRESHOLD = 11

MODEL_CALL_DELAY_SECONDS = {
    "gemini-3.1-flash-lite": 5,  # 15 RPM floor is 4s; +1s safety margin
    "gemini-2.5-flash": 13,  # unchanged — RPD-bound not RPM-bound, rarely run, no pressure to optimize
}
DEFAULT_CALL_DELAY = 13  # fallback if GEMINI_MODEL is something unrecognized — stay conservative, not permissive


def select_active_panelist(session: DefenseSession) -> Panelist:
    """v0.3f Decision 3, replacing fixed round-robin: a panelist who surfaced a
    weakness keeps the floor for follow-ups (Decision 2); new-topic rotation only
    advances among domain panelists who haven't yet opened their own topic
    (Decision 1); Devil's Advocate fires exactly once, after every domain panelist
    has spoken (Decision 4).

    Rotation is derived entirely from `session` state, not a turn counter — the
    vestigial `turn_num` parameter (kept temporarily because `main.py` called this
    positionally) is dropped now that `main.py` is being repaired anyway
    (v0.3e/f cleanup)."""
    if _should_follow_up(session):
        prev_key = session.turns[-1].panelist_archetype_key
        return next(p for p in session.panel if p.archetype_key == prev_key)

    domain_panelists = [p for p in session.panel if p.archetype_key != DEVILS_ADVOCATE_KEY]
    domain_spoken = {
        t.panelist_archetype_key for t in session.turns if t.panelist_archetype_key != DEVILS_ADVOCATE_KEY
    }
    if all(p.archetype_key in domain_spoken for p in domain_panelists):
        return next(p for p in session.panel if p.archetype_key == DEVILS_ADVOCATE_KEY)

    return domain_panelists[len(session.used_chunk_indices) % len(domain_panelists)]


def _clamp_difficulty(value: int) -> int:
    return max(1, min(5, value))


def _is_strong_answer(score: AnswerScore) -> bool:
    """Strong = no weak dimension (every quality axis >= 3) AND solidly high overall
    (clarity + depth + grounding >= 11 of 15). Deliberately not keyed on difficulty_delta:
    under the 'press on weakness' rubric a weak-but-engaged answer also escalates (+1), so
    only the sub-scores separate strong from weak. The all-axes-plus-sum test tolerates the
    model's per-axis noise (a genuinely strong answer may dip to 3 on one axis) without
    admitting a uniformly mediocre 3/3/3 or a fluent-but-ungrounded answer."""
    return (
        min(score.clarity, score.depth, score.grounding) >= 3
        and (score.clarity + score.depth + score.grounding) >= STRONG_ANSWER_SUM_THRESHOLD
    )


def _render_prior_exchange_framing(previous_turn: ConversationTurn, active_panelist: Panelist) -> str:
    """Fills FOLLOWUP_SYSTEM_PROMPT's {prior_exchange_framing} slot (v0.3d, Decision 1/2 —
    see docs/v0.3d-followup-attribution-fix.md). `select_active_panelist` round-robins by
    turn number and `_should_follow_up` presses a weakness regardless of who asked it, so
    the two fire independently — most follow-ups land on a colleague of the original asker,
    not the asker themselves. The old unconditional "You previously asked..." wording put a
    false first-person claim in the model's mouth whenever that happened. Compares
    archetype_key (identity), not panelist_name (denormalized flavor)."""
    if previous_turn.panelist_archetype_key == active_panelist.archetype_key:
        return (
            'You previously asked the candidate this question:\n'
            '"""\n'
            f'{previous_turn.question}\n'
            '"""\n\n'
            'The candidate answered:\n'
            '"""\n'
            f'{previous_turn.answer}\n'
            '"""'
        )
    return (
        f'Your fellow panelist, Dr. {previous_turn.panelist_name}, asked the candidate this question:\n'
        '"""\n'
        f'{previous_turn.question}\n'
        '"""\n\n'
        'The candidate answered:\n'
        '"""\n'
        f'{previous_turn.answer}\n'
        '"""\n\n'
        'You are now taking the floor. Press on the identified weakness from your own\n'
        "lane's perspective — do not simply repeat your colleague's question or imitate\n"
        'their framing.'
    )


def _should_follow_up(session: DefenseSession) -> bool:
    """Follow up only when the prior answer had a real weakness to press: a gap was named
    AND the answer was not strong. A strong answer advances to a new topic even if the
    scorer noted a residual gap — primary_gap stays honest for the v0.3 report, and
    branching no longer collapses to 'always follow up' now that the adversarial rubric
    surfaces a gap on nearly every answer.

    Hard cap on top of the strength check: once MAX_FOLLOW_UPS_PER_TOPIC follow-ups have
    already been spent on the current chunk, force the new-topic branch regardless of
    answer strength — a defense shouldn't read as an endless cross-examination on one
    point. This only overrides which branch fires; the difficulty/scoring math is untouched.

    Unchanged by round-robin (v0.3b): this looks at the immediately preceding turn
    regardless of which panelist asked it or who is asking next — any panelist may press
    a weakness surfaced by whoever went before them."""
    previous_turn = session.turns[-1] if session.turns else None
    if previous_turn is None or previous_turn.score is None:
        return False
    if previous_turn.score.primary_gap is None:
        return False
    if session.follow_ups_on_current_topic >= MAX_FOLLOW_UPS_PER_TOPIC:
        return False
    return not _is_strong_answer(previous_turn.score)


def t_max(session: DefenseSession) -> int:
    """v0.3f Decision 6: a computed backstop, not a chosen number — 1 initial question
    + MAX_FOLLOW_UPS_PER_TOPIC(2) follow-ups per panelist, worst case. A defensive
    circuit-breaker against a bug or genuine infinite loop, not the primary
    termination condition (that's `session_is_complete`'s Decision 5 check). With
    v0.3e's uniform 4-total panel cap this is 12 for every session."""
    return 3 * len(session.panel)


def session_is_complete(session: DefenseSession) -> bool:
    """v0.3f Decision 5 (CONFIRMED by Sean 2026-07-17): the session ends the moment
    Devil's Advocate's own follow-up chain concludes — DA has spoken, and either the
    candidate's answer to DA was strong (no follow-up triggered) or
    MAX_FOLLOW_UPS_PER_TOPIC was reached on DA's own challenge. No second round; no
    loop-back to domain panelists after DA.

    `t_max` is checked first as the circuit-breaker backstop (Decision 6) — under
    correct operation it never fires before the condition below does."""
    if len(session.turns) >= t_max(session):
        return True
    last_turn = session.turns[-1] if session.turns else None
    if last_turn is None or last_turn.panelist_archetype_key != DEVILS_ADVOCATE_KEY:
        return False
    return not _should_follow_up(session)


# Difficulty floor for is_grounded() enforcement escalation (v0.3 hardening, Decision 2).
# Below this, behavior is unchanged from v0.2.5: warn-and-log only, no retry.
GROUNDING_ENFORCEMENT_DIFFICULTY_FLOOR = 4


def _generate_with_grounding_enforcement(
    provider: LLMProvider,
    prompt: str,
    chunk_text: str,
    difficulty_level: int,
    model: str,
) -> tuple[PanelistQuestion, bool, bool]:
    """One generation call, checked against `is_grounded()`. Below difficulty 4: unchanged
    v0.2.5 behavior — warn and log, serve as-is. At difficulty >= 4: a failure triggers
    exactly one retry — same prompt (same chunk, same target difficulty, no re-retrieval),
    a fresh generation call (Decision 2). Returns (question, grounding_retry_used,
    grounding_flagged); grounding_flagged is True only when the retry also fails — still
    served, just flagged (Decision 4).
    """

    def _call() -> PanelistQuestion:
        result = provider.generate_structured(prompt, PanelistQuestion)
        time.sleep(MODEL_CALL_DELAY_SECONDS.get(model, DEFAULT_CALL_DELAY))
        return result

    question = _call()
    if is_grounded(question.grounding_reference, chunk_text):
        return question, False, False

    logger.warning(
        "Grounding check failed (difficulty %d): reference %r not found in chunk. Chunk excerpt: %r",
        difficulty_level,
        question.grounding_reference,
        chunk_text[:200],
    )
    if difficulty_level < GROUNDING_ENFORCEMENT_DIFFICULTY_FLOOR:
        return question, False, False

    retry_question = _call()
    retry_grounded = is_grounded(retry_question.grounding_reference, chunk_text)
    if not retry_grounded:
        logger.warning(
            "Grounding retry also failed (difficulty %d): reference %r not found in chunk. "
            "Serving anyway, flagged. Chunk excerpt: %r",
            difficulty_level,
            retry_question.grounding_reference,
            chunk_text[:200],
        )
    return retry_question, True, not retry_grounded


def _generate_da_question(
    provider: LLMProvider,
    session: DefenseSession,
    panelist: Panelist,
    model: str,
) -> ConversationTurn:
    """Devil's Advocate's distinct question path (Decision 2): no retrieval, no
    follow-up-on-own-topic — it selects the highest-scored prior claim from another
    panelist and contests it, re-grounded in that claim's own document excerpt.
    Selection is Python-side (deterministic, testable), not left to the model to browse
    the digest and guess. `max()` with a key returns the first max on ties, so selection
    is reproducible turn to turn."""
    candidates = [
        t for t in session.turns if t.score is not None and t.panelist_archetype_key != DEVILS_ADVOCATE_KEY
    ]
    if not candidates:
        raise RuntimeError("Devil's Advocate has no prior scored turn to contest — DA must not fire on turn 1.")
    target_turn = max(candidates, key=turn_total_score)
    target_archetype = ARCHETYPE_CONFIG[target_turn.panelist_archetype_key]

    prompt = DEVILS_ADVOCATE_SYSTEM_PROMPT.format(
        panelist_name=panelist.panelist_name,
        persona_framing=panelist.persona_framing,
        defense_type=session.profile.defense_type.value,
        digest_block=render_digest_block(session),
        target_panelist_name=target_turn.panelist_name,
        target_archetype_title=target_archetype["archetype_title"],
        target_question=target_turn.question,
        target_answer_summary=target_turn.score.answer_summary,
        retrieved_chunk=target_turn.chunk_text,
        difficulty_level=session.difficulty_current,
    )
    print(
        f"[branch: devils-advocate — contesting {target_turn.panelist_archetype_key}'s "
        f"chunk {target_turn.chunk_index} (score {turn_total_score(target_turn)}/15)]"
    )

    # Same standing grounding check as every other panelist — no adversarial exemption
    # (Decision 2, Task 3). DA's grounding_reference is checked against the SAME chunk
    # the original claim was grounded in, since that is "the source document" for this
    # contested claim. At difficulty >= 4, a failure escalates to one retry (Decision 2).
    panelist_question, retry_used, flagged = _generate_with_grounding_enforcement(
        provider, prompt, target_turn.chunk_text, session.difficulty_current, model
    )

    return ConversationTurn(
        panelist_archetype_key=panelist.archetype_key,
        panelist_name=panelist.panelist_name,
        question=panelist_question.question,
        grounding_reference=panelist_question.grounding_reference,
        chunk_index=target_turn.chunk_index,
        chunk_text=target_turn.chunk_text,
        difficulty_level=session.difficulty_current,
        grounding_retry_used=retry_used,
        grounding_flagged=flagged,
    )


def _generate_question(
    provider: LLMProvider,
    session: DefenseSession,
    chunks: list[Chunk],
    embedding_model: EmbeddingModel,
    panelist: Panelist,
    other_subtype_line: str,
    model: str,
) -> ConversationTurn:
    if panelist.archetype_key == DEVILS_ADVOCATE_KEY:
        return _generate_da_question(provider, session, panelist, model)

    archetype = ARCHETYPE_CONFIG[panelist.archetype_key]
    previous_turn = session.turns[-1] if session.turns else None
    is_followup = _should_follow_up(session)
    digest_block = render_digest_block(session)
    # v0.3 hardening, Decision 3 — populated only at difficulty >= 4, empty string otherwise.
    high_difficulty_guard = (
        HIGH_DIFFICULTY_GROUNDING_GUARD
        if session.difficulty_current >= GROUNDING_ENFORCEMENT_DIFFICULTY_FLOOR
        else ""
    )

    if not is_followup:
        query = archetype["archetype_focus"]
        results = retrieve(
            query,
            chunks,
            top_k=1,
            embedding_model=embedding_model,
            exclude_indices=frozenset(session.used_chunk_indices),
        )
        if not results:
            raise RuntimeError("Retrieval returned no chunks — document may be exhausted.")
        chunk_index, chunk = results[0]
        chunk_text = chunk.text
        # Acknowledgment fields: empty on turn 1 (nothing to react to yet), populated on
        # every later new-topic turn — whether we pivoted because the answer was strong or
        # because the follow-up cap forced it. Same conditional-field pattern as other_subtype_line.
        if previous_turn is not None and previous_turn.answer is not None:
            previous_answer_line = PREVIOUS_ANSWER_LINE.format(previous_answer=previous_turn.answer)
            acknowledgment_instruction = ACKNOWLEDGMENT_INSTRUCTION
        else:
            previous_answer_line = ""
            acknowledgment_instruction = ""
        prompt = PANELIST_SYSTEM_PROMPT.format(
            panelist_name=panelist.panelist_name,
            persona_framing=panelist.persona_framing,
            archetype_title=archetype["archetype_title"],
            archetype_focus=archetype["archetype_focus"],
            archetype_lane=archetype["archetype_lane"],
            defense_type=session.profile.defense_type.value,
            other_subtype_line=other_subtype_line,
            domain=session.profile.domain,
            topic=session.profile.topic,
            difficulty_level=session.difficulty_current,
            retrieved_chunk=chunk_text,
            previous_answer_line=previous_answer_line,
            acknowledgment_instruction=acknowledgment_instruction,
            digest_block=digest_block,
            high_difficulty_guard=high_difficulty_guard,
        )
        print(f"[branch: new-topic — chunk {chunk_index}]")
    else:
        assert previous_turn is not None and previous_turn.score is not None
        chunk_index, chunk_text = previous_turn.chunk_index, previous_turn.chunk_text
        prompt = FOLLOWUP_SYSTEM_PROMPT.format(
            panelist_name=panelist.panelist_name,
            persona_framing=panelist.persona_framing,
            archetype_title=archetype["archetype_title"],
            archetype_focus=archetype["archetype_focus"],
            archetype_lane=archetype["archetype_lane"],
            defense_type=session.profile.defense_type.value,
            prior_exchange_framing=_render_prior_exchange_framing(previous_turn, panelist),
            primary_gap=previous_turn.score.primary_gap,
            retrieved_chunk=chunk_text,
            difficulty_level=session.difficulty_current,
            digest_block=digest_block,
            high_difficulty_guard=high_difficulty_guard,
        )
        print(f"[branch: follow-up — primary_gap: \"{previous_turn.score.primary_gap}\" — reusing chunk {chunk_index}]")

    # Standing grounding check (both question paths converge here), escalated at
    # difficulty >= 4 to one retry (v0.3 hardening, Decision 2). A miss below the floor is
    # unchanged v0.2.5 behavior: signal to collect, not a crash — warn and continue.
    panelist_question, retry_used, flagged = _generate_with_grounding_enforcement(
        provider, prompt, chunk_text, session.difficulty_current, model
    )

    return ConversationTurn(
        panelist_archetype_key=panelist.archetype_key,
        panelist_name=panelist.panelist_name,
        question=panelist_question.question,
        grounding_reference=panelist_question.grounding_reference,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        difficulty_level=session.difficulty_current,
        grounding_retry_used=retry_used,
        grounding_flagged=flagged,
    )


def _score_answer(
    provider: LLMProvider,
    turn: ConversationTurn,
    panelist: Panelist,
    defense_type: str,
) -> AnswerScore:
    archetype = ARCHETYPE_CONFIG[panelist.archetype_key]
    prompt = SCORING_SYSTEM_PROMPT.format(
        defense_type=defense_type,
        panelist_name=panelist.panelist_name,
        archetype_title=archetype["archetype_title"],
        question=turn.question,
        answer=turn.answer,
        retrieved_chunk=turn.chunk_text,
    )
    return provider.generate_structured(prompt, AnswerScore)
