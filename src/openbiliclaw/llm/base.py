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

LLM_CONNECTIVITY_PROBE_MAX_TOKENS = 4096


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


def classify_llm_unavailability(exc: BaseException) -> str | None:
    """Classify an exception chain as an expected-transient LLM outage.

    Walks the ``__cause__`` / ``__context__`` chain (cycle-safe) and returns:

    - ``"rate_limited"`` when any link is an :class:`LLMRateLimitError` or
      carries a "rate limit" message — a provider is cooling down and the
      caller should simply retry on its next cycle.
    - ``"no_provider"`` when any :class:`LLMFallbackError` /
      ``LLMProviderExecutionError`` in the chain reports that no provider was
      available (typically during guided init, before a chat LLM is
      configured).
    - ``None`` for anything else — a genuine error the caller should keep
      logging loudly.

    ``rate_limited`` wins when both apply: an "all providers failed … rate
    limit" fallback wraps a rate-limit cause and should read as backoff, not a
    missing provider.
    """
    kind = classify_llm_failure_kind(exc)
    return kind if kind in {"rate_limited", "no_provider"} else None


# Substrings that mark an upstream content-moderation / compliance refusal.
# Chinese compat gateways (e.g. iFlytek code 10013) return the refusal *as a
# 500*, so we cannot key off the HTTP status — we sniff the message instead.
_LLM_MODERATION_MARKERS = (
    "法律法规",
    "健康和谐",
    "无法提供关于",
    "内容审查",
    "content policy",
    "content_filter",
    "content management",
    "risk_control",
    "10013",
)

_LLM_AUTH_MARKERS = ("authentication", "unauthorized", "invalid api key", "401")
_LLM_QUOTA_MARKERS = (
    "rate limit",
    "insufficient_quota",
    "insufficient quota",
    "quota",
    "exhausted",
    "429",
)

_LLM_TIMEOUT_MARKERS = ("timeout", "timed out", "deadline exceeded")
_LLM_INVALID_RESPONSE_MARKERS = (
    "empty response",
    "empty completion",
    "invalid response",
    "expected scored json",
)


def classify_llm_failure_kind(exc: BaseException) -> str | None:
    """Return a machine-readable LLM failure kind from an exception chain.

    The chain walk is cycle-safe. Specific provider throttling and missing
    provider states win over coarser timeout/response classifications.
    """

    # Lazily imported to avoid a circular import (service imports this module).
    from openbiliclaw.llm.service import LLMProviderExecutionError

    seen: set[int] = set()
    current: BaseException | None = exc
    rate_limited = no_provider = auth_failed = False
    timed_out = invalid_response = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if isinstance(current, LLMRateLimitError) or any(
            marker in message for marker in _LLM_QUOTA_MARKERS
        ):
            rate_limited = True
        if isinstance(current, LLMFallbackError | LLMProviderExecutionError) and (
            "no provider was available" in message
        ):
            no_provider = True
        if any(marker in message for marker in _LLM_AUTH_MARKERS):
            auth_failed = True
        if isinstance(current, (LLMTimeoutError, TimeoutError)) or any(
            marker in message for marker in _LLM_TIMEOUT_MARKERS
        ):
            timed_out = True
        if isinstance(current, LLMResponseError) or any(
            marker in message for marker in _LLM_INVALID_RESPONSE_MARKERS
        ):
            invalid_response = True
        current = current.__cause__ or current.__context__
    if rate_limited:
        return "rate_limited"
    if no_provider:
        return "no_provider"
    if auth_failed:
        return "auth_failed"
    if timed_out:
        return "timeout"
    if invalid_response:
        return "invalid_response"
    return None


def describe_llm_failure(exc: BaseException) -> str | None:
    """Translate an LLM exception chain into a short, human-readable Chinese
    reason suitable for page-side display during guided init.

    Walks the ``__cause__`` / ``__context__`` chain (cycle-safe) and returns a
    one-line explanation the user can act on — a content-moderation refusal,
    authentication failure, exhausted provider/fallback chain, rate limiting,
    a timeout, or an empty response. Returns ``None`` when the chain carries no
    recognizable LLM signal, so callers can fall back to their own generic
    message.

    Ordering is by specificity: a moderation refusal is the most actionable
    (switch models), so it wins over the coarser transient buckets.
    """
    # Lazily imported to avoid a circular import (service imports this module).
    from openbiliclaw.llm.service import LLMProviderExecutionError

    seen: set[int] = set()
    current: BaseException | None = exc
    moderation = auth_failed = rate_limited = False
    timed_out = no_provider = empty_response = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if any(marker.lower() in message for marker in _LLM_MODERATION_MARKERS):
            moderation = True
        if any(marker in message for marker in _LLM_AUTH_MARKERS):
            auth_failed = True
        if isinstance(current, LLMRateLimitError) or any(
            marker in message for marker in _LLM_QUOTA_MARKERS
        ):
            rate_limited = True
        if isinstance(current, LLMTimeoutError) or "timed out" in message:
            timed_out = True
        if isinstance(current, LLMFallbackError | LLMProviderExecutionError) and (
            "no provider was available" in message
        ):
            no_provider = True
        if isinstance(current, LLMResponseError):
            empty_response = True
        current = current.__cause__ or current.__context__

    if moderation:
        return (
            "AI 服务上游因内容合规策略拒绝了本次请求；可更换一个不带内容审查的模型 / 服务商后重试。"
        )
    if auth_failed:
        return (
            "AI 服务鉴权失败（HTTP 401），API key 可能填错或已失效。"
            "请到设置页检查 LLM provider 的 API key 后重试。"
        )
    if rate_limited:
        return (
            "AI 服务额度用尽或被限流（HTTP 429）。请检查 LLM provider 的余额 / 套餐，"
            "或在设置里配置一个备选 provider 兜底后重试。"
        )
    if timed_out:
        return "AI 服务响应超时；请检查网络连通性或稍后重试。"
    if no_provider:
        return (
            "没有可用的 AI 服务：主 Provider 与备用 Provider 都调用失败，"
            "请检查 LLM 配置、密钥与网络。"
        )
    if empty_response:
        return "AI 服务返回了空响应或无法解析的内容；请更换模型或稍后重试。"
    return None


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

    # Subclasses set True if they implement an ``async embed()`` method
    # backed by a working embeddings endpoint. Used by
    # ``build_embedding_service`` to pick a fallback when the user's
    # primary provider has no embedding API (e.g. Anthropic Claude,
    # DeepSeek). ``hasattr(provider, "embed")`` is unreliable because
    # subclassing OpenAIProvider auto-inherits ``embed`` even for
    # vendors whose backend doesn't actually expose it.
    supports_embedding: bool = False

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
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: Chat messages in OpenAI format [{role, content}].
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            json_mode: Whether to request structured JSON output.
            reasoning_effort: Per-call override for the provider's
                ``reasoning_effort`` setting (currently honoured by
                DeepSeek; ignored by other providers). ``None`` means
                "use the provider's configured default";
                ``""`` means "explicitly disable thinking for this
                call" (used by structured tasks like discovery's
                ``_evaluate_batch`` that don't benefit from reasoning).
            model: Optional per-call model override. Empty/whitespace
                values fall back to the provider's configured default
                without mutating provider state.

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
            # Reasoning-first OpenAI-compatible backends may spend the
            # initial output budget on reasoning before emitting content.
            # Keep the connectivity probe small, but not so tiny that those
            # providers get truncated before they can return visible content.
            resp = await self.complete(
                [{"role": "user", "content": "hi"}],
                max_tokens=LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
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
        # A non-empty fallback_provider IS the enable switch — there is no
        # separate boolean (the legacy [llm].fallback_enabled flag was never
        # consulted and has been removed; empty provider = fallback off).
        self.fallback_provider: str = ""
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

    def is_chat_capable(self, name: str) -> bool:
        """Return whether *name* is registered for chat completions."""
        target = name.strip().lower()
        return bool(target and target in self._providers and target not in self._chat_disabled)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Execute a completion request with sequential provider fallback."""
        last_error: Exception | None = None
        attempted: list[str] = []
        order = self._fallback_order()

        for position, provider_name in enumerate(order):
            has_next = position + 1 < len(order)
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
                    reasoning_effort=reasoning_effort,
                )
                self._rate_limited_until.pop(provider_name, None)
                return response
            except LLMRateLimitError as exc:
                last_error = exc
                self._mark_rate_limited(provider_name)
                self._log_provider_failure(provider_name, has_next=has_next)
            # LLMResponseError (empty/malformed content — flaky gateways
            # commonly die by returning 200 with no content) falls through to
            # the next provider like any other failure: the provider already
            # did its own single in-place retry, and a different provider may
            # well answer the same prompt.
            except (LLMProviderError, LLMTimeoutError) as exc:
                last_error = exc
                self._log_provider_failure(provider_name, has_next=has_next)

        attempted_list = ", ".join(attempted)
        if last_error is None:
            raise LLMFallbackError("No provider was available to process the request.")
        raise LLMFallbackError(
            f"All providers failed ({attempted_list}). Last error: {last_error}"
        ) from last_error

    @staticmethod
    def _log_provider_failure(provider_name: str, *, has_next: bool) -> None:
        if has_next:
            logger.warning("Provider %s failed, trying next fallback.", provider_name)
        else:
            logger.warning("Provider %s failed; no fallback provider left to try.", provider_name)

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Execute a completion against one exact chat-capable provider.

        Unlike ``complete()``, this method intentionally has no fallback
        chain. It is used for explicit per-module overrides where
        falling back to a different provider would violate user intent.
        """
        target = provider_name.strip().lower()
        if not self.is_chat_capable(target):
            available = ", ".join(self._fallback_order())
            raise LLMFallbackError(
                f"LLM provider '{target or provider_name}' is not registered "
                f"or not chat-capable. Chat-capable providers: {available}"
            )
        if self._provider_on_cooldown(target):
            logger.warning("Provider %s is cooling down after rate limit.", target)
            raise LLMRateLimitError(f"Provider {target} is cooling down after rate limit.")

        provider = self.get(target)
        try:
            response = await provider.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
                model=model,
            )
            self._rate_limited_until.pop(target, None)
            return response
        except LLMRateLimitError:
            self._mark_rate_limited(target)
            logger.warning("Provider %s rate-limited exact routed call.", target)
            raise

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
        whenever it's chat-capable. A fallback provider is included only
        when ``fallback_provider`` names a registered chat provider; no
        automatic provider walk is performed.
        """
        chat_pool = [name for name in self.available_providers if name not in self._chat_disabled]
        if not chat_pool:
            # Edge case: every provider is embedding-only. Surface the
            # problem rather than silently doing nothing — complete()
            # will raise LLMFallbackError("No provider was available
            # to process the request.").
            return []
        if self._default and self._default in chat_pool:
            ordered = [
                self._default,
                *[name for name in chat_pool if name != self._default],
            ]
        else:
            ordered = chat_pool
        fallback_provider = self.fallback_provider.strip().lower()
        if not fallback_provider:
            return ordered[:1]
        if fallback_provider == ordered[0] or fallback_provider not in chat_pool:
            return ordered[:1]
        return [ordered[0], fallback_provider]

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
