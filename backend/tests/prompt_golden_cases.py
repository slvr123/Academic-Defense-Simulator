"""M16 Decision 2's byte-identical gate: fixed inputs for every question prompt M16 touches.

Each case drives v1's own question generator with a recording provider and returns the
exact prompt string the model would have received. The golden files beside this module
were captured by running it on the parent commit of M16's v1 change, before the
`{language_line}` slot existed:

    python -m backend.tests.prompt_golden_cases --capture

The inputs are synthetic (no passage text from any real document), and the cases cover
every conditional slot of the three templates: the turn-1 and acknowledged new-topic
variants, the high-difficulty guard, the other-subtype line, the digest, both follow-up
framings (same panelist and colleague), and the Devil's Advocate.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from academic_defense_simulator.engine import _generate_question
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType, OtherSubtype
from academic_defense_simulator.models.panelist import Panelist
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.session import ConversationTurn, DefenseSession
from academic_defense_simulator.rag.retrieval import Chunk
from backend.closing import closing_prompt

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "m16_prompt_golden"

CHUNK_A = (
    "The prototype classified 412 leaf images into four disease classes. "
    "Accuracy on the held-out set was 91.3 percent, measured against labels from two agronomists."
)
CHUNK_B = (
    "Farmers were surveyed after one week of use. "
    "The questionnaire was read aloud in the local language by the researchers."
)
STRONG = AnswerScore(
    clarity=5, depth=4, grounding=5, difficulty_delta=1, primary_gap="No error analysis by class.",
    answer_summary="The candidate said the agronomists labeled every image independently.",
)
WEAK = AnswerScore(
    clarity=2, depth=1, grounding=1, difficulty_delta=1, primary_gap="No control for interviewer bias.",
    answer_summary="The candidate said the farmers were honest.",
)


class RecordingProvider:
    """Records each prompt and answers with a question grounded in the case's chunk."""

    def __init__(self, chunk_text: str) -> None:
        self.prompts: list[str] = []
        self._reference = chunk_text.split(". ")[0]

    def generate_structured(self, prompt: str, schema: type[PanelistQuestion]) -> PanelistQuestion:
        self.prompts.append(prompt)
        return schema(question="Q?", grounding_reference=self._reference, difficulty_level=3)


def panelist(key: str, name: str) -> Panelist:
    return Panelist(
        archetype_key=key, panelist_name=name, icon="x",
        persona_framing=f"You are known for pressing on {key.replace('_', ' ')}.",
    )


METHODS = panelist("methodology_expert", "Reyes")
RESULTS = panelist("results_conclusions_reviewer", "Okafor")
ETHICS = panelist("ethics_practicality_reviewer", "Haddad")
DEVIL = panelist("devils_advocate", "Lindqvist")


def profile(other: bool = False) -> DefenseProfile:
    return DefenseProfile(
        defense_type=DefenseType.OTHER if other else DefenseType.THESIS,
        other_subtype=OtherSubtype.GRANT_DEFENSE if other else None,
        domain="Agricultural Informatics", topic="Leaf disease detection for smallholder farms",
        selected_archetypes=["methodology_expert", "ethics_practicality_reviewer"]
        if other else ["methodology_expert", "results_conclusions_reviewer"],
        document_id="doc-golden",
    )


def turn(who: Panelist, chunk_index: int, chunk_text: str, score: AnswerScore | None, answer: str) -> ConversationTurn:
    return ConversationTurn(
        panelist_archetype_key=who.archetype_key, panelist_name=who.panelist_name,
        question=f"Dr. {who.panelist_name} asked about chunk {chunk_index}.",
        grounding_reference=chunk_text.split(". ")[0], chunk_index=chunk_index, chunk_text=chunk_text,
        difficulty_level=2, answer=answer, score=score,
    )


def render(session: DefenseSession, who: Panelist, other_subtype_line: str, chunk_text: str, **kwargs: Any) -> str:
    """The one prompt v1's generator sends for this state. `kwargs` go straight to
    `_generate_question`, so the test can pass `language_line` where the capture can't."""
    provider = RecordingProvider(chunk_text)
    chunk = Chunk(text=chunk_text, embedding=[0.0])

    def retriever(*_args: Any, **_kw: Any) -> list[tuple[int, Chunk]]:
        return [(7, chunk)]

    _generate_question(
        provider, session, [chunk], None, who, other_subtype_line, "golden-model",  # type: ignore[arg-type]
        retriever=retriever, **kwargs,
    )
    assert len(provider.prompts) == 1
    return provider.prompts[0]


def _new_topic_turn_one(**kw: Any) -> str:
    session = DefenseSession(profile=profile(), panel=[METHODS, RESULTS, DEVIL], difficulty_current=2)
    return render(session, METHODS, "", CHUNK_A, **kw)


def _new_topic_acknowledged_hard_other(**kw: Any) -> str:
    session = DefenseSession(
        profile=profile(other=True), panel=[METHODS, ETHICS], difficulty_current=4,
        turns=[turn(METHODS, 3, CHUNK_A, STRONG, "Two agronomists labeled each image on their own.")],
    )
    return render(session, ETHICS, " / grant_defense", CHUNK_B, **kw)


def _follow_up_same_panelist(**kw: Any) -> str:
    session = DefenseSession(
        profile=profile(), panel=[METHODS, RESULTS, DEVIL], difficulty_current=3,
        turns=[turn(METHODS, 5, CHUNK_B, WEAK, "I think they were honest with us.")],
    )
    return render(session, METHODS, "", CHUNK_B, **kw)


def _follow_up_colleague_hard(**kw: Any) -> str:
    session = DefenseSession(
        profile=profile(), panel=[METHODS, RESULTS, DEVIL], difficulty_current=4,
        turns=[
            turn(RESULTS, 3, CHUNK_A, STRONG, "Two agronomists labeled each image on their own."),
            turn(METHODS, 5, CHUNK_B, WEAK, "I think they were honest with us."),
        ],
    )
    return render(session, RESULTS, "", CHUNK_B, **kw)


def _devils_advocate(**kw: Any) -> str:
    session = DefenseSession(
        profile=profile(), panel=[METHODS, RESULTS, DEVIL], difficulty_current=4,
        turns=[
            turn(METHODS, 5, CHUNK_B, WEAK, "I think they were honest with us."),
            turn(RESULTS, 3, CHUNK_A, STRONG, "Two agronomists labeled each image on their own."),
        ],
    )
    return render(session, DEVIL, "", CHUNK_A, **kw)


def _closing_with_earlier_lines(**kw: Any) -> str:
    """M15's closing prompt (v2 side), which M16 gives a language slot too. It takes a
    `language`, not the slot's text, so a `language_line` here stands in for what the
    Taglish language would put in the slot."""
    args = (METHODS, profile(), "How did you control for interviewer bias?", "I think they were honest with us.",
            ["Thank you, that covers my questions on the scope."])
    if "language_line" not in kw:
        return closing_prompt(*args)
    import backend.closing
    from unittest.mock import patch

    with patch.object(backend.closing, "closing_language_line", lambda _language: kw["language_line"]):
        return closing_prompt(*args, language="taglish")


CASES: dict[str, Callable[..., str]] = {
    "panelist_turn1": _new_topic_turn_one,
    "panelist_ack_hard_other": _new_topic_acknowledged_hard_other,
    "followup_same_panelist": _follow_up_same_panelist,
    "followup_colleague_hard": _follow_up_colleague_hard,
    "devils_advocate": _devils_advocate,
    "closing_with_earlier_lines": _closing_with_earlier_lines,
}


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.txt"


def capture() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, case in CASES.items():
        prompt = case()
        golden_path(name).write_bytes(prompt.encode("utf-8"))
        print(f"{name}: {len(prompt.encode('utf-8'))} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", required=True)
    parser.parse_args()
    capture()
