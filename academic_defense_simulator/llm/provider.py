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
