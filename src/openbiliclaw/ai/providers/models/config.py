"""Typed non-secret configuration shared by all native PydanticAI providers."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import ConfigDict, Field
from pydantic_ai.settings import ModelSettings

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.core._pydantic import StrictBaseModel


class ProviderKind(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENROUTER = "openrouter"


class ModelOptions(StrictBaseModel):
    """Small reviewed option surface; unknown provider knobs are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    top_p: float | None = Field(default=None, gt=0, le=1)
    disable_thinking: bool = False

    def to_settings(self) -> ModelSettings:
        settings = ModelSettings()
        if self.temperature is not None:
            settings["temperature"] = self.temperature
        if self.max_tokens is not None:
            settings["max_tokens"] = self.max_tokens
        if self.top_p is not None:
            settings["top_p"] = self.top_p
        return settings


class ModelInstanceConfig(StrictBaseModel):
    """One model reached through PydanticAI's native provider layer."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    provider: ProviderKind
    model_name: str = Field(min_length=1)
    endpoint: str | None = Field(default=None, pattern=r"^https?://")
    secret_ref: str = Field(pattern=r"^cred_[0-9a-f]{32}$")
    options: ModelOptions = ModelOptions()
    capabilities: ModelCapabilities = ModelCapabilities()
    owner: str = Field(default="ai-runtime", min_length=1)
    provider_version: str = Field(default="1", min_length=1)

    def fingerprint(self) -> str:
        """Stable non-secret provider configuration fingerprint."""

        payload = {
            "provider": self.provider.value,
            "model": self.model_name,
            "endpoint": self.endpoint,
            "options": {
                "temperature": self.options.temperature,
                "max_tokens": self.options.max_tokens,
                "top_p": self.options.top_p,
                "disable_thinking": self.options.disable_thinking,
            },
            "capabilities": {
                "tools": self.capabilities.tools,
                "structured_output": self.capabilities.structured_output,
                "vision": self.capabilities.vision,
                "context_tokens": self.capabilities.context_tokens,
                "streaming": self.capabilities.streaming,
                "reasoning": self.capabilities.reasoning,
            },
            "owner": self.owner,
            "version": self.provider_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
