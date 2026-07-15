"""Anthropic Messages-protocol adapter for configured connections."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import anthropic
import httpx
from anthropic import AsyncAnthropic

from .base import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)

if TYPE_CHECKING:
    from anthropic.types import Message, MessageParam


class _AnthropicRetryableError(LLMProviderError):
    """A fixed-text transient failure eligible for the local retry loop."""


class _AnthropicPermanentError(LLMProviderError):
    """A fixed-text failure that must return without local retries."""


class AnthropicCompatibleProvider(LLMProvider):
    """One Anthropic Messages implementation for official and custom endpoints."""

    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(
        self,
        *,
        connection_id: str,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 300.0,
        proxy: str = "",
        trust_env: bool = True,
    ) -> None:
        self._connection_id = connection_id
        self._model = model
        self.base_url = base_url.strip()
        self._proxy = proxy.strip()
        self._trust_env = bool(trust_env and not self._proxy)
        client_kwargs: dict[str, Any] = {}
        if self._proxy or not self._trust_env:
            httpx_kwargs: dict[str, Any] = {
                "timeout": timeout,
                "trust_env": self._trust_env,
            }
            if self._proxy:
                httpx_kwargs["proxy"] = self._proxy
            client_kwargs["http_client"] = httpx.AsyncClient(**httpx_kwargs)
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=timeout,
            base_url=self.base_url,
            **client_kwargs,
        )

    @property
    def name(self) -> str:
        return self._connection_id

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
        del json_mode, reasoning_effort
        effective_model = (model or "").strip() or self._model
        system = ""
        chat_messages: list[dict[str, str]] = []
        for message in messages:
            if message["role"] == "system":
                system = message["content"]
            else:
                chat_messages.append(message)
        response = cast(
            "Message",
            await self._request_with_retry(
                model=effective_model,
                max_tokens=max_tokens,
                system=self._render_system_param(system or "You are a helpful assistant."),
                messages=chat_messages,
                temperature=temperature,
            ),
        )
        content = "".join(str(block.text) for block in response.content if hasattr(block, "text"))
        if not content.strip():
            raise LLMResponseError(f"{self.name} returned empty content")

        cache_read = int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
        cache_create = int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": (
                response.usage.input_tokens
                + cache_read
                + cache_create
                + response.usage.output_tokens
            ),
        }
        if cache_read:
            usage["cached_input_tokens"] = cache_read
        if cache_create:
            usage["cache_creation_input_tokens"] = cache_create
        return LLMResponse(
            content=content,
            model=response.model,
            provider=self.name,
            usage=usage,
            raw=response,
        )

    @staticmethod
    def _render_system_param(system_text: str) -> list[dict[str, object]]:
        return [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        last_error: LLMProviderError | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await self._client.messages.create(
                    model=cast("str", kwargs["model"]),
                    max_tokens=cast("int", kwargs["max_tokens"]),
                    system=kwargs["system"],
                    messages=cast("list[MessageParam]", kwargs["messages"]),
                    temperature=cast("float", kwargs["temperature"]),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                mapped = self._map_error(exc)
                last_error = mapped
            if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                break
            await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)
        if last_error is None:  # pragma: no cover - loop invariant guard
            raise LLMProviderError(f"{self.name} request failed")
        raise last_error from None

    def _map_error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, LLMRateLimitError):
            return LLMRateLimitError(f"{self.name} rate limit exceeded")
        if isinstance(
            exc,
            (LLMTimeoutError, TimeoutError, httpx.TimeoutException, anthropic.APITimeoutError),
        ):
            return LLMTimeoutError(f"{self.name} request timed out")
        status_code = self._safe_status_code(exc)
        if isinstance(exc, anthropic.RateLimitError) or status_code == 429:
            return LLMRateLimitError(f"{self.name} rate limit exceeded")
        if isinstance(exc, anthropic.AuthenticationError) or status_code == 401:
            return _AnthropicPermanentError(f"{self.name} authentication failed")
        if isinstance(exc, anthropic.PermissionDeniedError) or status_code == 403:
            return _AnthropicPermanentError(f"{self.name} permission denied")
        if status_code is not None and 400 <= status_code < 500:
            return _AnthropicPermanentError(f"{self.name} request failed: HTTP {status_code}")
        if status_code is not None and status_code >= 500:
            return _AnthropicRetryableError(f"{self.name} server error: HTTP {status_code}")
        if isinstance(
            exc,
            (anthropic.APIConnectionError, httpx.TransportError, ConnectionError),
        ):
            return _AnthropicRetryableError(f"{self.name} connection failed")
        if isinstance(exc, LLMResponseError):
            return _AnthropicPermanentError(f"{self.name} returned an invalid response")
        return _AnthropicPermanentError(f"{self.name} request failed")

    @staticmethod
    def _safe_status_code(exc: Exception) -> int | None:
        try:
            value = getattr(exc, "status_code", None)
        except Exception:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _is_retryable(exc: LLMProviderError) -> bool:
        return isinstance(exc, (LLMTimeoutError, _AnthropicRetryableError))
