"""Stable agent identities and explicit model capability requirements."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentId:
    """Stable identity shared by routing, usage, and evaluation."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("agent ID must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ModelRequirements:
    """Capabilities a domain agent requires from every routed model."""

    tools: bool = False
    structured_output: bool = False
    vision: bool = False
    context_tokens: int = 0
    streaming: bool = False
    reasoning: bool = False

    def __post_init__(self) -> None:
        if self.context_tokens < 0:
            raise ValueError("required context tokens must not be negative")


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Capabilities advertised by one configured model instance."""

    tools: bool = False
    structured_output: bool = False
    vision: bool = False
    context_tokens: int = 0
    streaming: bool = False
    reasoning: bool = False

    def __post_init__(self) -> None:
        if self.context_tokens < 0:
            raise ValueError("context tokens must not be negative")

    def satisfies(self, requirements: ModelRequirements) -> bool:
        """Return whether all mandatory requirements are supported."""

        return (
            (not requirements.tools or self.tools)
            and (not requirements.structured_output or self.structured_output)
            and (not requirements.vision or self.vision)
            and self.context_tokens >= requirements.context_tokens
            and (not requirements.streaming or self.streaming)
            and (not requirements.reasoning or self.reasoning)
        )
