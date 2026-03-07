"""Shared service facade for prompt assembly and LLM execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .base import LLMProviderError
from .prompts import build_socratic_dialogue_prompt

if TYPE_CHECKING:
    from openbiliclaw.memory.manager import MemoryManager

    from .base import LLMResponse


class SupportsComplete(Protocol):
    """Protocol for providers or registries with a complete method."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse: ...


class LLMServiceError(Exception):
    """Base exception for service-layer LLM errors."""


class LLMResponseContentError(LLMServiceError):
    """Raised when an LLM call returns empty content."""


class LLMProviderExecutionError(LLMServiceError):
    """Raised when the underlying provider or registry call fails."""


@dataclass
class LLMService:
    """Facade that assembles prompts and delegates calls to the registry."""

    registry: SupportsComplete
    memory: MemoryManager

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
    ) -> LLMResponse:
        """Generate a Socratic dialogue reply using core memory context."""
        messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            core_memory_text=self.memory.render_core_memory_prompt(),
            history=history,
        )
        try:
            response = await self.registry.complete(messages)
        except LLMProviderError as exc:
            raise LLMProviderExecutionError(str(exc)) from exc
        if not response.content.strip():
            raise LLMResponseContentError("LLM returned an empty response.")
        return response
