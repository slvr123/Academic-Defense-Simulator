"""A scripted stand-in for Gemini, for defense tests. It answers each call by the
response model asked for, records every call through the defense's CallCounter
the way GeminiProvider does, and can be told to fail or block on a given call."""

from __future__ import annotations

import re
import threading
from collections import defaultdict

from academic_defense_simulator.llm.provider import CallCounter
from academic_defense_simulator.models.answer_score import AnswerScore
from academic_defense_simulator.models.document_profile_extraction import DocumentProfileExtraction
from academic_defense_simulator.models.panelist import GeneratedPanelist, PanelGeneration
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from academic_defense_simulator.models.report import AnswerSuggestion, AnswerSuggestionList
from backend.defense import Providers

# In every paragraph of conftest.make_pdf, so every question passes is_grounded.
GROUNDING = "ISO/IEC 25010"
ROLES = {
    DocumentProfileExtraction: "extraction",
    PanelGeneration: "persona",
    PanelistQuestion: "question",
    AnswerScore: "scoring",
    AnswerSuggestionList: "suggestions",
}


class FakeGemini:
    """One per test. `providers(calls)` is the ProvidersFactory the app takes."""

    def __init__(self) -> None:
        self.strong_answers = True  # weak answers (with a gap) make panelists follow up
        self.calls_by_role: dict[str, int] = defaultdict(int)
        self.failures: dict[str, list[Exception]] = defaultdict(list)  # role -> raised in order
        self.block: dict[str, threading.Event] = {}  # role -> wait for this before answering
        self.entered: dict[str, threading.Event] = defaultdict(threading.Event)
        self.questions_asked = 0
        self.question_prompts: list[str] = []  # every question prompt, in call order

    def fail_next(self, role: str, *errors: Exception) -> None:
        self.failures[role].extend(errors)

    def providers(self, calls: CallCounter) -> Providers:
        return Providers(defense=_Provider(self, calls, "defense"), suggestions=_Provider(self, calls, "suggestions"))

    def respond(self, role: str, prompt: str):
        self.calls_by_role[role] += 1
        self.entered[role].set()
        if role in self.block:
            self.block[role].wait(10)
        if self.failures[role]:
            raise self.failures[role].pop(0)
        if role == "extraction":
            return DocumentProfileExtraction(domain="Library science", topic="A library management system")
        if role == "persona":
            keys = re.findall(r"^- (\w+): ", prompt, re.MULTILINE)
            return PanelGeneration(
                panelists=[GeneratedPanelist(archetype_key=k, panelist_name=f"Panelist{i}", persona_framing="f")
                           for i, k in enumerate(keys)]
            )
        if role == "question":
            self.questions_asked += 1
            self.question_prompts.append(prompt)
            return PanelistQuestion(
                question=f"Question {self.questions_asked}?", grounding_reference=GROUNDING, difficulty_level=2
            )
        if role == "scoring":
            points = 5 if self.strong_answers else 2
            return AnswerScore(
                clarity=points, depth=points, grounding=points, difficulty_delta=1 if self.strong_answers else 0,
                primary_gap="no evidence offered", answer_summary="The candidate claimed the instrument is valid.",
            )
        if role == "suggestions":
            return AnswerSuggestionList(
                suggestions=[AnswerSuggestion(turn_index=0, suggestion="Cite the evaluation.", grounding_reference=GROUNDING)]
            )
        if role == "narrative":
            return "The candidate defended the instrument choice well."
        raise AssertionError(f"unexpected role {role}")


class _Provider:
    def __init__(self, fake: FakeGemini, calls: CallCounter, label: str) -> None:
        self._fake, self._calls, self._label = fake, calls, label

    def generate_structured(self, prompt, response_model):
        self._calls.record(self._label)
        return self._fake.respond(ROLES[response_model], prompt)

    def generate_text(self, prompt):
        self._calls.record(self._label)
        return self._fake.respond("narrative", prompt)
