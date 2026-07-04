"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        """Send a fully-rendered prompt, return a validated instance of response_model.

        Implementations own all provider-specific request/response shape handling.
        """
        ...
