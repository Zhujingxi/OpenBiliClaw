"""LLM base interfaces and provider registry.

Defines the abstract LLM provider interface and a registry for
dynamically selecting and switching between providers.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

class LLMProviderError(Exception):
    """Base exception for provider request failures."""


class LLMRateLimitError(LLMProviderError):
    """Raised when a provider rate-limits a request."""


class LLMTimeoutError(LLMProviderError):
    """Raised when a provider request times out."""


class LLMResponseError(LLMProviderError):
    """Raised when a provider returns an invalid or empty response."""


class LLMFallbackError(LLMProviderError):
    """Raised when all candidate providers fail."""


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    content: str = ""
    model: str = ""
    provider: str = ""
    usage: dict[str, int] | None = None  # token counts
    raw: Any = None  # Raw provider response
    tool_calls: list[dict[str, Any]] | None = None  # Phase 4: function calling


@dataclass
class HealthCheckResult:
    """Availability result for one provider."""

    available: bool
    is_default: bool = False
    error: str | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement a unified interface so the agent
    can switch between them transparently.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name identifier."""
        ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: Chat messages in OpenAI format [{role, content}].
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            json_mode: Whether to request structured JSON output.

        Returns:
            Standardized LLMResponse.
        """
        ...

    async def health_check(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if the provider is available.
        """
        try:
            resp = await self.complete(
                [{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            return bool(resp.content)
        except Exception:
            logger.exception("Health check failed for %s", self.name)
            return False


class LLMRegistry:
    """Registry for LLM providers.

    Supports dynamic registration and selection of providers.
    """

    _RATE_LIMIT_COOLDOWN_SECONDS = 60.0

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._default: str = ""
        self._rate_limited_until: dict[str, float] = {}
        # Names of providers that should NOT appear in the chat-completion
        # fallback chain — typically an Ollama instance registered solely
        # for embedding (see register(..., chat_capable=False)).
        self._chat_disabled: set[str] = set()

    def register(
        self,
        provider: LLMProvider,
        *,
        default: bool = False,
        chat_capable: bool = True,
    ) -> None:
        """Register a provider.

        Args:
            provider: LLM provider instance.
            default: Whether to set as default provider.
            chat_capable: When False, the provider is registered for
                non-chat use (typically Ollama for embedding-only) and
                will NOT appear in the chat-completion fallback chain.
                Default True for backward compat — every other call site
                wants chat capability.

                Why this matters: if the user only set
                ``[llm.embedding] provider = "ollama"`` and never
                configured ``[llm.ollama] model``, the embedding service
                still needs Ollama to be in the registry — but the
                model on disk is ``bge-m3``, which can't serve
                ``/api/chat`` requests. Without this flag, when the
                primary cloud provider hits a transient error, the
                fallback chain happily picks Ollama, gets a 404 from
                ``/api/chat``, and the user sees
                ``All providers failed (openai, ollama)``.
        """
        self._providers[provider.name] = provider
        if not chat_capable:
            self._chat_disabled.add(provider.name)
        else:
            self._chat_disabled.discard(provider.name)
        if default or not self._default:
            self._default = provider.name
        logger.info(
            "Registered LLM provider: %s%s%s",
            provider.name,
            " (default)" if default else "",
            "" if chat_capable else " [embedding-only]",
        )

    def get(self, name: str | None = None) -> LLMProvider:
        """Get a provider by name, or the default.

        Args:
            name: Provider name. If None, returns the default.

        Returns:
            LLM provider instance.

        Raises:
            KeyError: If the provider is not registered.
        """
        target = name or self._default
        if target not in self._providers:
            available = ", ".join(self._providers.keys())
            raise KeyError(f"LLM provider '{target}' not found. Available: {available}")
        return self._providers[target]

    @property
    def available_providers(self) -> list[str]:
        """List of registered provider names."""
        return list(self._providers.keys())

    @property
    def default_provider(self) -> str:
        """Name of the default provider."""
        return self._default

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Execute a completion request with sequential provider fallback."""
        last_error: Exception | None = None
        attempted: list[str] = []

        for provider_name in self._fallback_order():
            attempted.append(provider_name)
            if self._provider_on_cooldown(provider_name):
                last_error = LLMRateLimitError(
                    f"Provider {provider_name} is cooling down after rate limit."
                )
                logger.warning("Provider %s is cooling down after rate limit.", provider_name)
                continue
            provider = self.get(provider_name)
            try:
                response = await provider.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
                self._rate_limited_until.pop(provider_name, None)
                return response
            except LLMResponseError:
                raise
            except LLMRateLimitError as exc:
                last_error = exc
                self._mark_rate_limited(provider_name)
                logger.warning("Provider %s failed, trying next fallback.", provider_name)
            except (LLMProviderError, LLMTimeoutError) as exc:
                last_error = exc
                logger.warning("Provider %s failed, trying next fallback.", provider_name)

        attempted_list = ", ".join(attempted)
        if last_error is None:
            raise LLMFallbackError("No provider was available to process the request.")
        raise LLMFallbackError(
            f"All providers failed ({attempted_list}). Last error: {last_error}"
        ) from last_error

    async def health_check_all(self) -> dict[str, HealthCheckResult]:
        """Run health checks for all registered providers."""
        results: dict[str, HealthCheckResult] = {}
        for provider_name in self.available_providers:
            provider = self.get(provider_name)
            try:
                available = await provider.health_check()
                results[provider_name] = HealthCheckResult(
                    available=available,
                    is_default=provider_name == self._default,
                    error=None if available else "health check returned false",
                )
            except Exception as exc:
                results[provider_name] = HealthCheckResult(
                    available=False,
                    is_default=provider_name == self._default,
                    error=str(exc),
                )
        return results

    def _fallback_order(self) -> list[str]:
        """Return the sequential CHAT-fallback provider order.

        Skips providers registered with ``chat_capable=False`` (the
        embedding-only Ollama case). The default provider is honored
        whenever it's chat-capable; if the user picked an embedding-only
        provider as default we still skip it from the chat chain.
        """
        chat_pool = [
            name for name in self.available_providers
            if name not in self._chat_disabled
        ]
        if not chat_pool:
            # Edge case: every provider is embedding-only. Surface the
            # problem rather than silently doing nothing — complete()
            # will raise LLMFallbackError("No provider was available
            # to process the request.").
            return []
        if self._default and self._default in chat_pool:
            return [
                self._default,
                *[name for name in chat_pool if name != self._default],
            ]
        return chat_pool

    def _provider_on_cooldown(self, provider_name: str) -> bool:
        until = self._rate_limited_until.get(provider_name)
        if until is None:
            return False
        if until > time.monotonic():
            return True
        self._rate_limited_until.pop(provider_name, None)
        return False

    def _mark_rate_limited(self, provider_name: str) -> None:
        self._rate_limited_until[provider_name] = (
            time.monotonic() + self._RATE_LIMIT_COOLDOWN_SECONDS
        )
