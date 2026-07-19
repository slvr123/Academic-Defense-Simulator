"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """Raised when a provider call fails cleanly (timeout, unparseable response,
    or exhausted quota) after any retry this boundary allows. Callers should stop
    the session, not skip the turn — a session with a silently dropped turn isn't
    a valid transcript."""


@dataclass
class CallCounter:
    """Per-session LLM call tally (v0.3h Brief). Provider-agnostic, no streamlit
    import — a fresh instance is created per user session (in the Streamlit layer)
    and threaded into each provider construction alongside a stage label.

    Deliberately NOT incremented at business-logic call sites: a call site that
    retries internally (GeminiProvider's own retry-once on transient errors, or a
    business-logic retry loop like `extract_document_profile`'s) would otherwise
    only ever register once, silently undercounting what actually went over the
    wire. `record()` is called from inside the provider implementation itself, once
    per real network attempt, so every retry is counted."""

    total: int = 0
    by_stage: dict[str, int] = field(default_factory=dict)

    def record(self, label: str) -> None:
        self.total += 1
        self.by_stage[label] = self.by_stage.get(label, 0) + 1


class LLMProvider(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        """Send a fully-rendered prompt, return a validated instance of response_model.

        Implementations own all provider-specific request/response shape handling.
        """
        ...

    @abstractmethod
    def generate_text(self, prompt: str) -> str:
        """Send a fully-rendered prompt, return the raw text response — no schema, no
        parsing. Used only where the output is prose, not structured data (v0.3c report
        narrative): demanding JSON there would add a parse failure mode for zero benefit.
        """
        ...
