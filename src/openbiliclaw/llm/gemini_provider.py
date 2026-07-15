"""Gemini Developer API provider built on the official google-genai SDK."""

from __future__ import annotations

import asyncio
from typing import Any, NoReturn

from .base import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)

genai: Any | None
errors: Any | None
types: Any | None
_SDK_IMPORT_ERROR: str | None

try:
    from google import genai as _genai
    from google.genai import errors as _errors
    from google.genai import types as _types
except ImportError as _exc:  # pragma: no cover - exercised via subprocess regression test
    # ImportError (not just ModuleNotFoundError): the SDK may be installed yet
    # fail to load when a native transitive dep breaks — e.g. cryptography's
    # manylinux wheel can't dlopen under Termux/Android Bionic (issue #80).
    # Either way the provider must degrade instead of crashing CLI startup.
    genai = None
    errors = None
    types = None
    _SDK_IMPORT_ERROR = str(_exc)
else:
    genai = _genai
    errors = _errors
    types = _types
    _SDK_IMPORT_ERROR = None


def gemini_sdk_available() -> bool:
    """Return whether the optional google-genai dependency is installed and loadable."""
    return genai is not None and types is not None


def _raise_missing_sdk() -> NoReturn:
    detail = f" (import failed: {_SDK_IMPORT_ERROR})" if _SDK_IMPORT_ERROR else ""
    raise LLMProviderError(
        "Gemini provider requires the optional dependency 'google-genai' to be "
        f"installed and loadable.{detail}"
    )


class GeminiProvider(LLMProvider):
    """Gemini provider using the official Gemini Developer API client."""

    supports_embedding = True
    # Class can implement image embed; actual readiness depends on the
    # embedding model name (gemini-embedding-2 family only).
    supports_image_embedding = True

    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25
    _MULTIMODAL_EMBEDDING_MARKERS = ("gemini-embedding-2",)

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout: float = 300.0,
        base_url: str = "",
        embedding_output_dimensionality: int | None = None,
        proxy: str = "",
        trust_env: bool = True,
        provider_name: str = "gemini",
    ) -> None:
        if not gemini_sdk_available():
            _raise_missing_sdk()
        assert genai is not None
        self._provider_name = provider_name
        self._model = model
        self._embedding_output_dimensionality = (
            embedding_output_dimensionality
            if embedding_output_dimensionality is not None and embedding_output_dimensionality > 0
            else None
        )
        http_options: dict[str, Any] = {"timeout": int(timeout * 1000)}
        normalized_base_url = (base_url or "").strip()
        if normalized_base_url:
            http_options["base_url"] = normalized_base_url.rstrip("/") + "/"
        # google-genai passes these args to its underlying httpx clients.
        self._proxy = proxy.strip()
        self._trust_env = bool(trust_env and not self._proxy)
        if self._proxy or not self._trust_env:
            transport_args: dict[str, Any] = {"trust_env": self._trust_env}
            if self._proxy:
                transport_args["proxy"] = self._proxy
            http_options["client_args"] = dict(transport_args)
            http_options["async_client_args"] = dict(transport_args)
        self._client = genai.Client(
            api_key=api_key,
            http_options=http_options,
        )

    @staticmethod
    def _is_reasoning_first_model(model: str) -> bool:
        """Whether the model belongs to the reasoning-first family that
        REJECTS ``thinking_budget=0``.

        Background: the ``thinking_budget=0`` hack is a 2.5-flash cost
        optimisation — it tells Gemini "don't spend tokens thinking".
        Gemini 3.x Pro / 3.x Flash and 2.5-pro are reasoning-first
        models; Google rejects ``thinking_budget=0`` on them with
        ``400 INVALID_ARGUMENT`` ("Thinking budget X is invalid for
        model Y"). Symptom: the first call may sneak through, but
        json_mode call sites (discovery / soul structured tasks) all
        400 immediately.

        The check is intentionally name-based (no SDK call): preview /
        GA / dated revisions all share the same family prefix.
        """
        m = model.lower()
        # Gemini 3.x: 3-pro / 3-flash / 3.1-pro / 3.1-flash-lite-preview / ...
        if m.startswith("gemini-3"):
            return True
        # 2.5-pro is reasoning-first too; 2.5-flash is the only 2.5
        # variant that legitimately accepts thinking_budget=0.
        return m.startswith("gemini-2.5-pro")

    @property
    def name(self) -> str:
        return self._provider_name

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
        # ``reasoning_effort`` is DeepSeek-specific. Gemini has its own
        # ``thinking_config`` that's already auto-disabled in JSON mode.
        # Accept the kwarg for signature compatibility but no-op here.
        del reasoning_effort
        if types is None:
            _raise_missing_sdk()
        effective_model = (model or "").strip() or self._model
        # ``thinking_budget=0`` is a 2.5-flash cost saver. Reasoning-first
        # models (3.x family, 2.5-pro) reject it with 400 INVALID_ARGUMENT
        # — see _is_reasoning_first_model. Skip the hack on those.
        thinking_config = None
        if json_mode and not self._is_reasoning_first_model(effective_model):
            thinking_config = types.ThinkingConfig(thinking_budget=0)
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
            thinking_config=thinking_config,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = await self._request_with_retry(
            model=effective_model,
            contents=self._render_messages(messages),
            config=config,
        )

        content = response.text or ""
        if not content.strip():
            raise LLMResponseError(f"{self._provider_name} returned empty content")

        usage = None
        if response.usage_metadata is not None:
            usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count or 0,
                "completion_tokens": response.usage_metadata.candidates_token_count or 0,
                "total_tokens": response.usage_metadata.total_token_count or 0,
            }
            # Gemini exposes cached_content_token_count when a previously
            # uploaded explicit cache (Context Caching API) was used.
            # Normalize under the universal ``cached_input_tokens`` key.
            cached = int(getattr(response.usage_metadata, "cached_content_token_count", 0) or 0)
            if cached:
                usage["cached_input_tokens"] = cached

        return LLMResponse(
            content=content,
            model=response.model_version or effective_model,
            provider=self._provider_name,
            usage=usage,
            raw=response,
        )

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await self._client.aio.models.generate_content(**kwargs)
            except Exception as exc:
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                    raise mapped from exc
                await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError(f"{self._provider_name} request failed")
        raise last_error

    def _map_error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, LLMProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError(f"{self._provider_name} request timed out")

        status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        message = (getattr(exc, "message", None) or str(exc)).lower()
        if status_code == 429 or "rate limit" in message or "resource_exhausted" in message:
            return LLMRateLimitError(f"{self._provider_name} rate limit exceeded")
        if (errors is not None and isinstance(exc, errors.ServerError)) or (
            status_code and int(status_code) >= 500
        ):
            return LLMProviderError(f"{self._provider_name} server error: {status_code}")
        return LLMProviderError(f"{self._provider_name} request failed: {exc}")

    def _is_retryable(self, exc: LLMProviderError) -> bool:
        if isinstance(exc, LLMRateLimitError):
            return False
        return isinstance(exc, (LLMProviderError, LLMTimeoutError))

    @classmethod
    def is_multimodal_embedding_model(cls, model: str) -> bool:
        """Return whether *model* maps text and images into one space."""
        name = (model or "").strip().lower()
        if not name:
            return False
        return any(marker in name for marker in cls._MULTIMODAL_EMBEDDING_MARKERS)

    def _embed_content_config(self) -> Any:
        if types is None:
            _raise_missing_sdk()
        config_kwargs: dict[str, Any] = {"task_type": "SEMANTIC_SIMILARITY"}
        if self._embedding_output_dimensionality is not None:
            config_kwargs["output_dimensionality"] = self._embedding_output_dimensionality
        return types.EmbedContentConfig(**config_kwargs)

    @staticmethod
    def _embedding_values(response: Any) -> list[float]:
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            return []
        values = getattr(embeddings[0], "values", None)
        if values is None:
            return []
        return list(values)

    async def embed(self, text: str, *, model: str = "gemini-embedding-001") -> list[float]:
        """Get text embedding using Gemini's embedding model.

        Args:
            text: Text to embed.
            model: Embedding model name (default: gemini-embedding-001).

        Returns:
            Embedding vector (dimension depends on model / config).
        """
        if types is None:
            _raise_missing_sdk()
        response = await self._client.aio.models.embed_content(
            model=model,
            contents=text,
            config=self._embed_content_config(),
        )
        return self._embedding_values(response)

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        model: str = "gemini-embedding-2",
    ) -> list[float]:
        """Get image-only embedding (Gemini Embedding 2 multimodal space).

        Requires a multimodal embedding model. Returns ``[]`` when the
        model is text-only so callers can degrade without raising.
        """
        if types is None:
            _raise_missing_sdk()
        if not self.is_multimodal_embedding_model(model):
            return []
        if not image_bytes:
            return []
        part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=(mime_type or "image/jpeg").strip() or "image/jpeg",
        )
        response = await self._client.aio.models.embed_content(
            model=model,
            contents=part,
            config=self._embed_content_config(),
        )
        return self._embedding_values(response)

    def _render_messages(self, messages: list[dict[str, str]]) -> str:
        chunks: list[str] = []
        for message in messages:
            content = message["content"].strip()
            if not content:
                continue
            role = message["role"].upper()
            chunks.append(f"[{role}]\n{content}")
        return "\n\n".join(chunks)
