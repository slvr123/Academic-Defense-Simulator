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
    PANELIST_SYSTEM_PROMPT,
    PREVIOUS_ANSWER_LINE,
    SCORING_SYSTEM_PROMPT,
)
from academic_defense_simulator.rag.embeddings import EmbeddingModel
from academic_defense_simulator.rag.retrieval import Chunk, retrieve

logger = logging.getLogger(__name__)

MAX_TURNS = 6
MAX_BLANK_ATTEMPTS = 3
MAX_FOLLOW_UPS_PER_TOPIC = 2  # hard cap: after this many follow-ups on one chunk, force a new topic

MODEL_CALL_DELAY_SECONDS = {
    "gemini-3.1-flash-lite": 5,  # 15 RPM floor is 4s; +1s safety margin
    "gemini-2.5-flash": 13,  # unchanged — RPD-bound not RPM-bound, rarely run, no pressure to optimize
}
DEFAULT_CALL_DELAY = 13  # fallback if GEMINI_MODEL is something unrecognized — stay conservative, not permissive


def select_active_panelist(session: DefenseSession, turn_num: int) -> Panelist:
    """Fixed round-robin: one turn per panelist per round, cycling `session.panel` in
    composition order. Devil's Advocate sits last in `session.panel` (see
    `panel.compose_full_roster`), so it naturally fires last in every round. No
    score-driven handoff, no floor retention — that is v0.3.x, gated on this being
    verified first (Decision 3)."""
    return session.panel[(turn_num - 1) % len(session.panel)]


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
        and (score.clarity + score.depth + score.grounding) >= 11
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

    panelist_question: PanelistQuestion = provider.generate_structured(prompt, PanelistQuestion)
    time.sleep(MODEL_CALL_DELAY_SECONDS.get(model, DEFAULT_CALL_DELAY))

    # Same standing grounding check as every other panelist — no adversarial exemption
    # (Decision 2, Task 3). DA's grounding_reference is checked against the SAME chunk
    # the original claim was grounded in, since that is "the source document" for this
    # contested claim.
    if not is_grounded(panelist_question.grounding_reference, target_turn.chunk_text):
        logger.warning(
            "Grounding check failed on Devil's Advocate turn (chunk %d): reference %r not found in chunk. "
            "Chunk excerpt: %r",
            target_turn.chunk_index,
            panelist_question.grounding_reference,
            target_turn.chunk_text[:200],
        )

    return ConversationTurn(
        panelist_archetype_key=panelist.archetype_key,
        panelist_name=panelist.panelist_name,
        question=panelist_question.question,
        grounding_reference=panelist_question.grounding_reference,
        chunk_index=target_turn.chunk_index,
        chunk_text=target_turn.chunk_text,
        difficulty_level=session.difficulty_current,
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
            previous_question=previous_turn.question,
            previous_answer=previous_turn.answer,
            primary_gap=previous_turn.score.primary_gap,
            retrieved_chunk=chunk_text,
            difficulty_level=session.difficulty_current,
            digest_block=digest_block,
        )
        print(f"[branch: follow-up — primary_gap: \"{previous_turn.score.primary_gap}\" — reusing chunk {chunk_index}]")

    panelist_question: PanelistQuestion = provider.generate_structured(prompt, PanelistQuestion)
    time.sleep(MODEL_CALL_DELAY_SECONDS.get(model, DEFAULT_CALL_DELAY))

    # Standing grounding check (both question paths converge here). A miss means the
    # panelist cited a phrase that isn't in the chunk — signal to collect, not a crash:
    # warn and continue, never fail the session.
    if not is_grounded(panelist_question.grounding_reference, chunk_text):
        logger.warning(
            "Grounding check failed on chunk %d: reference %r not found in chunk. "
            "Chunk excerpt: %r",
            chunk_index,
            panelist_question.grounding_reference,
            chunk_text[:200],
        )

    return ConversationTurn(
        panelist_archetype_key=panelist.archetype_key,
        panelist_name=panelist.panelist_name,
        question=panelist_question.question,
        grounding_reference=panelist_question.grounding_reference,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        difficulty_level=session.difficulty_current,
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
