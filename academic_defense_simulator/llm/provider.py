"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """Raised when a provider call fails cleanly (timeout, unparseable response,
    or exhausted quota) after any retry this boundary allows. Callers should stop
    the session, not skip the turn — a session with a silently dropped turn isn't
    a valid transcript."""


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
