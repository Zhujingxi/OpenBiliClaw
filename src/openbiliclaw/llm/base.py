"""Provider-neutral LLM interfaces and failure classification."""

from __future__ import annotations

import errno
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)

LLM_CONNECTIVITY_PROBE_MAX_TOKENS = 4096


class LLMProviderError(Exception):
    """Base exception for provider request failures."""


class LLMRateLimitError(LLMProviderError):
    """Raised when a provider rate-limits a request."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        normalized = normalize_retry_after_seconds(retry_after_seconds)
        self.retry_after_seconds = normalized
        # Existing background coordinators inspect ``retry_after``. Keep the
        # alias until their Task 8 cutover so one parsed provider header drives
        # both the ordered route circuit and the legacy scheduler backoff.
        self.retry_after = normalized


class LLMTimeoutError(LLMProviderError):
    """Raised when a provider request times out."""


class LLMResponseError(LLMProviderError):
    """Raised when a provider returns an invalid or empty response."""


class LLMFallbackError(LLMProviderError):
    """Raised when all candidate providers fail."""


def normalize_retry_after_seconds(
    value: object,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse a positive Retry-After delta or HTTP date without retaining input."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds > 0 else None
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        seconds = float(candidate)
    except ValueError:
        seconds = 0.0
    if math.isfinite(seconds) and seconds > 0:
        return seconds
    try:
        parsed = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    delay = (parsed - current).total_seconds()
    return delay if math.isfinite(delay) and delay > 0 else None


def retry_after_seconds_from_exception(exc: BaseException) -> float | None:
    """Extract a safe Retry-After value from a mapped or SDK exception."""
    for attribute in ("retry_after_seconds", "retry_after"):
        try:
            value = getattr(exc, attribute, None)
        except Exception:
            continue
        parsed = normalize_retry_after_seconds(value)
        if parsed is not None:
            return parsed

    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
    except Exception:
        return None
    if headers is None:
        return None
    for key in ("retry-after", "Retry-After"):
        try:
            value = headers.get(key)
        except Exception:
            continue
        parsed = normalize_retry_after_seconds(value)
        if parsed is not None:
            return parsed
    return None


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
    - ``"model_not_found"`` when the provider reachably answered but the
      configured model does not exist (a local Ollama model never pulled → 404
      ``not_found_error``, or a wrong/inaccessible model name). Retrying won't
      help until the user pulls/renames the model, but the loop should log one
      calm actionable line rather than a full traceback.
    - ``None`` for anything else — a genuine error the caller should keep
      logging loudly.

    ``rate_limited`` wins when both apply: an "all providers failed … rate
    limit" fallback wraps a rate-limit cause and should read as backoff, not a
    missing provider.
    """
    kind = classify_llm_failure_kind(exc)
    return kind if kind in {"rate_limited", "no_provider", "model_not_found"} else None


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

_LLM_AUTH_MARKERS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "permission denied",
    "forbidden",
    "401",
    "http 403",
    "status 403",
)
# The provider host was reachable and answered, but the configured *model* is
# missing: a local Ollama model that was never pulled returns HTTP 404 with
# ``{"type": "not_found_error", "message": "model 'x' not found, try pulling it
# first"}``; OpenAI-compat 404s say ``the model 'x' does not exist``. Distinct
# from ``no_provider`` (no chat provider configured at all) and from auth (401):
# retrying is futile until the user pulls/renames the model, so callers should
# surface an actionable "pull the model / fix the name" hint, not a traceback.
_LLM_MODEL_NOT_FOUND_MARKERS = (
    "not_found_error",
    "try pulling it first",
    "no such model",
    "does not exist or you do not have access",
    "model does not exist",
    "http 404",
    "status 404",
)
_LLM_QUOTA_MARKERS = (
    "rate limit",
    "insufficient_quota",
    "insufficient quota",
    "quota",
    "exhausted",
    "429",
)

_LLM_TIMEOUT_MARKERS = ("timeout", "timed out", "deadline exceeded")
# SSL / certificate verification failures. Kept distinct from the generic
# connection markers because the actionable cause differs: a cert-verify
# failure on an otherwise-reachable host almost always means a local proxy /
# antivirus / firewall is doing HTTPS interception (or the endpoint uses a
# self-signed cert), so the user-facing hint points at the proxy, not the
# network. httpx raises ``ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]`` and
# the OpenAI SDK wraps it as ``APIConnectionError`` — neither subclasses
# Python's ``ConnectionError``, so we sniff the message.
_LLM_SSL_MARKERS = (
    "ssl:",
    "certificate verify failed",
    "certificate_verify_failed",
    "unable to get local issuer",
    "self-signed certificate",
    "self signed certificate",
    "sslcertverificationerror",
    "ssl handshake",
)
_LLM_CONNECTION_MARKERS = (
    "connection reset",
    "connection refused",
    "connection error",
    "connection aborted",
    "network is unreachable",
    "name resolution",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "max retries exceeded",
    "getaddrinfo failed",
)
_LLM_SERVER_ERROR_MARKERS = (
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "status 500",
    "status 502",
    "status 503",
    "status 504",
)
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
    moderation = rate_limited = no_provider = auth_failed = model_not_found = False
    timed_out = invalid_response = connection = server_error = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if any(marker.lower() in message for marker in _LLM_MODERATION_MARKERS):
            moderation = True
        try:
            attempts = getattr(current, "attempts", ())
        except Exception:
            attempts = ()
        for attempt in attempts if isinstance(attempts, tuple | list) else ():
            try:
                attempt_kind = str(getattr(attempt, "failure_kind", ""))
            except Exception:
                continue
            if attempt_kind == "moderation":
                moderation = True
            elif attempt_kind == "rate_limited":
                rate_limited = True
            elif attempt_kind == "auth_failed":
                auth_failed = True
            elif attempt_kind == "model_not_found":
                model_not_found = True
            elif attempt_kind == "timeout":
                timed_out = True
            elif attempt_kind == "connection":
                connection = True
            elif attempt_kind == "server_error":
                server_error = True
            elif attempt_kind == "invalid_response":
                invalid_response = True
        if isinstance(current, LLMRateLimitError) or any(
            marker in message for marker in _LLM_QUOTA_MARKERS
        ):
            rate_limited = True
        if isinstance(current, LLMFallbackError | LLMProviderExecutionError) and (
            "no provider was available" in message
        ):
            no_provider = True
        if any(marker in message for marker in _LLM_MODEL_NOT_FOUND_MARKERS) or (
            "model" in message and "not found" in message
        ):
            model_not_found = True
        if any(marker in message for marker in _LLM_AUTH_MARKERS):
            auth_failed = True
        if isinstance(current, (LLMTimeoutError, TimeoutError)) or any(
            marker in message for marker in _LLM_TIMEOUT_MARKERS
        ):
            timed_out = True
        network_errno = isinstance(current, OSError) and current.errno in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETUNREACH,
            errno.EHOSTDOWN,
            errno.EHOSTUNREACH,
            errno.ETIMEDOUT,
        }
        if (
            isinstance(current, ConnectionError)
            or network_errno
            or any(marker in message for marker in _LLM_CONNECTION_MARKERS)
            or any(marker in message for marker in _LLM_SSL_MARKERS)
        ):
            connection = True
        if any(marker in message for marker in _LLM_SERVER_ERROR_MARKERS):
            server_error = True
        if isinstance(current, LLMResponseError) or any(
            marker in message for marker in _LLM_INVALID_RESPONSE_MARKERS
        ):
            invalid_response = True
        current = current.__cause__ or current.__context__
    if moderation:
        return "moderation"
    if rate_limited:
        return "rate_limited"
    if no_provider:
        return "no_provider"
    if model_not_found:
        return "model_not_found"
    if auth_failed:
        return "auth_failed"
    if timed_out:
        return "timeout"
    if connection:
        return "connection"
    if server_error:
        return "server_error"
    if invalid_response:
        return "invalid_response"
    return None


def is_provider_scoped_failure(
    exc: BaseException,
    failure_kind: str | None = None,
) -> bool:
    """Return whether an embedding boundary may safely degrade or fallback.

    Typed provider failures are deliberate adapter-boundary outcomes. Untyped
    exceptions are accepted only when both their concrete transport type and
    shared classifier identify a supported provider/transport category, so a
    request ``ValueError`` or internal ``RuntimeError`` cannot be masked by
    message text alone.
    """
    if isinstance(exc, LLMProviderError):
        return True
    kind = failure_kind if failure_kind is not None else classify_llm_failure_kind(exc)
    return bool(
        kind
        in {
            "rate_limited",
            "auth_failed",
            "model_not_found",
            "timeout",
            "connection",
            "server_error",
            "invalid_response",
            "moderation",
        }
        and isinstance(exc, (TimeoutError, ConnectionError, OSError))
    )


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
    from openbiliclaw.llm.service import LLMProviderExecutionError, LLMResponseContentError

    seen: set[int] = set()
    current: BaseException | None = exc
    moderation = auth_failed = rate_limited = False
    timed_out = no_provider = empty_response = False
    ssl_failed = connect_failed = model_not_found = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if any(marker.lower() in message for marker in _LLM_MODERATION_MARKERS):
            moderation = True
        if any(marker in message for marker in _LLM_MODEL_NOT_FOUND_MARKERS) or (
            "model" in message and "not found" in message
        ):
            model_not_found = True
        if any(marker in message for marker in _LLM_AUTH_MARKERS):
            auth_failed = True
        if isinstance(current, LLMRateLimitError) or any(
            marker in message for marker in _LLM_QUOTA_MARKERS
        ):
            rate_limited = True
        if isinstance(current, LLMTimeoutError | TimeoutError) or "timed out" in message:
            timed_out = True
        if any(marker in message for marker in _LLM_SSL_MARKERS):
            ssl_failed = True
        network_errno = isinstance(current, OSError) and current.errno in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETUNREACH,
            errno.EHOSTDOWN,
            errno.EHOSTUNREACH,
            errno.ETIMEDOUT,
        }
        if (
            isinstance(current, ConnectionError)
            or network_errno
            or any(marker in message for marker in _LLM_CONNECTION_MARKERS)
        ):
            connect_failed = True
        if isinstance(current, LLMFallbackError | LLMProviderExecutionError) and (
            "no provider was available" in message
        ):
            no_provider = True
        if isinstance(current, LLMResponseError | LLMResponseContentError):
            empty_response = True
        current = current.__cause__ or current.__context__

    if moderation:
        return (
            "AI 服务上游因内容合规策略拒绝了本次请求；可更换一个不带内容审查的模型 / 服务商后重试。"
        )
    if model_not_found:
        return (
            "AI 服务找不到所配置的模型（HTTP 404）。本地 Ollama 模型可能尚未拉取"
            "（先执行 `ollama pull <模型名>`），或模型名 / 访问权限填错。"
            "请到设置页核对对话模型名称后重试。"
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
    if ssl_failed:
        return (
            "无法与 AI 服务建立安全连接（SSL 证书验证失败）。"
            "常见原因是本地代理 / 杀毒 / 防火墙对 HTTPS 做了中间人拦截，"
            "或接口地址使用了自签证书。请关闭代理（或把该接口地址加入直连白名单）后重试。"
        )
    if connect_failed:
        return (
            "无法连接到 AI 服务（网络连接失败）。"
            "请检查网络、接口地址是否正确，以及代理 / 防火墙设置后重试。"
        )
    if no_provider:
        return (
            "没有可用的 AI 服务：主 Provider 与备用 Provider 都调用失败，"
            "请检查 LLM 配置、密钥与网络。"
        )
    if empty_response:
        return "AI 服务返回了空响应或无法解析的内容；请更换模型或稍后重试。"
    return None


def safe_llm_failure_message(exc: BaseException) -> str:
    """Return actionable LLM failure copy without exposing upstream detail."""
    return describe_llm_failure(exc) or (
        "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。"
    )


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    content: str = ""
    model: str = ""
    provider: str = ""
    usage: dict[str, int] | None = None  # token counts
    raw: Any = None  # Raw provider response
    tool_calls: list[dict[str, Any]] | None = None  # Phase 4: function calling
    connection_id: str = ""
    connection_type: str = ""
    preset: str = ""
    route_position: int = 0


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement a unified interface so the agent
    can switch between them transparently.
    """

    # Subclasses set True if they implement a working embeddings endpoint.
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
