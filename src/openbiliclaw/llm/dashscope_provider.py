"""DashScope (Alibaba Model Studio) multimodal embedding provider.

Uses the native multimodal-embedding HTTP API — not OpenAI-compatible
``/v1/embeddings``. Text and image vectors share one space for models such
as ``qwen3-vl-embedding`` (independent modality mode = image-only path).

Chat completion is intentionally unsupported; this class is embedding-only.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from .base import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    normalize_retry_after_seconds,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
_EMBED_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
_BASE_URL_SUFFIXES = ("/compatible-mode/v1", "/api/v1", "/compatible-mode")

# qwen3-vl-embedding documented MRL dimensions (default 2560).
_QWEN3_VL_DIMENSIONS: frozenset[int] = frozenset({2560, 2048, 1536, 1024, 768, 512, 256})

_MULTIMODAL_MODEL_MARKERS: tuple[str, ...] = (
    "qwen3-vl-embedding",
    "qwen2.5-vl-embedding",
    "tongyi-embedding-vision",
    "multimodal-embedding",
)


class DashScopeEmbeddingProvider(LLMProvider):
    """Embedding-only DashScope multimodal provider (Qwen / Tongyi vision)."""

    supports_embedding = True
    supports_image_embedding = True
    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-vl-embedding",
        *,
        base_url: str = "",
        timeout: float = 120.0,
        embedding_output_dimensionality: int = 0,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise LLMProviderError("DashScope embedding requires a non-empty api_key")
        self._api_key = key
        self._model = (model or "qwen3-vl-embedding").strip() or "qwen3-vl-embedding"
        root = (base_url or _DEFAULT_BASE_URL).strip().rstrip("/")
        # Alibaba documents workspace-scoped native DashScope base URLs as
        # ``...maas.aliyuncs.com/api/v1``. Strip that prefix as one unit before
        # appending _EMBED_PATH; removing only ``/v1`` would produce the broken
        # ``/api/api/v1/services/...`` URL. Keep accepting compatible-mode URLs
        # as a convenience, but always call the native multimodal endpoint.
        for suffix in _BASE_URL_SUFFIXES:
            if root.endswith(suffix):
                root = root[: -len(suffix)].rstrip("/")
                break
        self._base_url = root or _DEFAULT_BASE_URL
        self._timeout = max(5.0, float(timeout))
        self._embedding_output_dimensionality = max(0, int(embedding_output_dimensionality or 0))

    @property
    def name(self) -> str:
        return "dashscope"

    @classmethod
    def is_multimodal_embedding_model(cls, model: str) -> bool:
        name = (model or "").strip().lower()
        if not name:
            return False
        return any(marker in name for marker in _MULTIMODAL_MODEL_MARKERS)

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
        raise LLMProviderError(
            "DashScopeEmbeddingProvider is embedding-only; use a chat provider "
            "(e.g. openai_compatible → dashscope compatible-mode) for completions."
        )

    async def embed(self, text: str, *, model: str = "qwen3-vl-embedding") -> list[float]:
        """Independent text embedding in the multimodal space."""
        content = (text or "").strip()
        if not content:
            return []
        return await self._embed_contents(
            [{"text": content}],
            model=model or self._model,
        )

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        model: str = "qwen3-vl-embedding",
    ) -> list[float]:
        """Independent image-only embedding (no text fusion)."""
        if not image_bytes:
            return []
        effective_model = model or self._model
        if not self.is_multimodal_embedding_model(effective_model):
            return []
        # qwen2.5-vl-embedding only supports fusion mode — image-only
        # independent vectors are not available on that model.
        if "qwen2.5-vl-embedding" in effective_model.lower():
            logger.warning(
                "DashScope model %r does not support independent image "
                "embeddings; use qwen3-vl-embedding or tongyi-embedding-vision-*",
                effective_model,
            )
            return []
        mime = (mime_type or "image/jpeg").strip().lower() or "image/jpeg"
        fmt = mime.split("/", 1)[1] if "/" in mime else mime
        if fmt in {"jpg", "jpe"}:
            fmt = "jpeg"
        data_uri = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        return await self._embed_contents(
            [{"image": data_uri}],
            model=effective_model,
        )

    def _dimension_for_model(self, model: str) -> int | None:
        dim = self._embedding_output_dimensionality
        if dim <= 0:
            return None
        name = (model or "").lower()
        if "qwen3-vl-embedding" in name:
            if dim in _QWEN3_VL_DIMENSIONS:
                return dim
            logger.debug(
                "DashScope dimension %s not in %s for %s; using model default",
                dim,
                sorted(_QWEN3_VL_DIMENSIONS),
                model,
            )
            return None
        # tongyi-embedding-vision-plus-2026-03-06 supports dimension; fixed
        # older plus/flash models do not — only pass for known flexible IDs.
        if "2026-03-06" in name and "tongyi-embedding-vision" in name:
            return dim
        return None

    async def _embed_contents(
        self,
        contents: list[dict[str, Any]],
        *,
        model: str,
    ) -> list[float]:
        url = f"{self._base_url}{_EMBED_PATH}"
        body: dict[str, Any] = {
            "model": model,
            "input": {"contents": contents},
        }
        params: dict[str, Any] = {}
        dimension = self._dimension_for_model(model)
        if dimension is not None:
            params["dimension"] = dimension
        # Independent modality vectors (image-only / text-only). Never set
        # enable_fusion=true on the main path.
        if params:
            body["parameters"] = params

        # Pitfall rule 1 / v0.3.166–167 outbound routing: route per endpoint via
        # network.httpx_kwargs_for_endpoint(base_url). DashScope's
        # dashscope.aliyuncs.com / dashscope.cn are on the domestic host list, so
        # they force trust_env=False (direct) even when [network].mode is
        # system/custom for reaching overseas models — a CN user's env proxy /
        # overseas ladder never tunnels the domestic embedding call (the exact
        # regression v0.3.167 fixed for chat gateways). A genuinely non-domestic
        # base_url still follows the global mode. Imported per-call, like codex_auth.
        response = await self._post_with_retry(url, body)
        invalid_json = False
        try:
            data = response.json()
        except (TypeError, ValueError):
            data = None
            invalid_json = True
        if invalid_json:
            raise LLMResponseError("dashscope embedding returned an invalid response") from None

        payload_error = self._payload_error(data)
        if payload_error is not None:
            raise payload_error from None

        return self._parse_embedding_vector(data)

    @staticmethod
    def _payload_error(data: object) -> LLMProviderError | None:
        """Map a successful-HTTP error envelope without retaining its message."""
        if not isinstance(data, dict) or not data.get("code") or data.get("output"):
            return None
        code = str(data.get("code") or "").lower().replace("_", "")
        if any(marker in code for marker in ("invalidapikey", "authentication", "unauthorized")):
            return LLMProviderError("dashscope embedding authentication failed")
        if any(marker in code for marker in ("throttl", "ratelimit", "quota", "limitexceeded")):
            return LLMRateLimitError("dashscope embedding rate limit exceeded")
        if "model" in code and any(marker in code for marker in ("notfound", "notexist")):
            return LLMProviderError("dashscope embedding model not found")
        return LLMProviderError("dashscope embedding provider error")

    async def _post_with_retry(self, url: str, body: dict[str, Any]) -> httpx.Response:
        """Send one embedding request under a bounded, secret-safe contract."""
        from openbiliclaw.network import httpx_kwargs_for_endpoint

        last_error: LLMProviderError | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            status_code: int | None = None
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, **httpx_kwargs_for_endpoint(self._base_url)
                ) as client:
                    response = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                status_code = response.status_code
                if status_code < 400:
                    return response
                mapped = self._http_error(response)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, httpx.TimeoutException):
                mapped = LLMTimeoutError("dashscope embedding request timed out")
            except httpx.TransportError:
                mapped = LLMProviderError("dashscope embedding connection error")
            except LLMProviderError as exc:
                mapped = exc
            except Exception:
                mapped = LLMProviderError("dashscope embedding request failed")

            last_error = mapped
            retryable = status_code is None or status_code >= 500
            if (
                isinstance(mapped, LLMRateLimitError)
                or not retryable
                or attempt == self._MAX_RETRIES
            ):
                break
            await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            last_error = LLMProviderError("dashscope embedding request failed")
        raise last_error

    @staticmethod
    def _http_error(response: httpx.Response) -> LLMProviderError:
        status = response.status_code
        if status == 429:
            retry_after = None
            try:
                retry_after = normalize_retry_after_seconds(response.headers.get("retry-after"))
            except Exception:
                retry_after = None
            return LLMRateLimitError(
                "dashscope embedding rate limit exceeded",
                retry_after_seconds=retry_after,
            )
        if status in {401, 403}:
            return LLMProviderError("dashscope embedding authentication failed")
        if status >= 500:
            return LLMProviderError(f"dashscope embedding server error: HTTP {status}")
        return LLMProviderError(f"dashscope embedding request failed: HTTP {status}")

    @staticmethod
    def _parse_embedding_vector(data: object) -> list[float]:
        if not isinstance(data, dict):
            return []
        output = data.get("output")
        if not isinstance(output, dict):
            return []
        embeddings = output.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            return []
        first = embeddings[0]
        if not isinstance(first, dict):
            return []
        raw = first.get("embedding")
        if not isinstance(raw, list):
            return []
        vector: list[float] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return []
            vector.append(float(item))
        return vector
