"""DashScope (Alibaba Model Studio) multimodal embedding provider.

Uses the native multimodal-embedding HTTP API — not OpenAI-compatible
``/v1/embeddings``. Text and image vectors share one space for models such
as ``qwen3-vl-embedding`` (independent modality mode = image-only path).

Chat completion is intentionally unsupported; this class is embedding-only.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .base import LLMProvider, LLMProviderError, LLMResponse

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
_EMBED_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

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
        if root.endswith("/v1"):
            # Users may paste the OpenAI-compat chat base_url; strip to API root.
            root = root[: -len("/v1")].rstrip("/")
        if root.endswith("/compatible-mode"):
            root = root[: -len("/compatible-mode")].rstrip("/")
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

        # Pitfall rule 1 / v0.3.166 outbound routing: an embedding API client
        # must honour [network].mode like every other outbound provider
        # (openai / gemini / claude / openrouter) instead of silently
        # inheriting HTTP_PROXY. Default mode "direct" → trust_env=False, so a
        # CN user whose env proxy is an overseas ladder never routes the
        # dashscope.aliyuncs.com call through it; "system"/"custom" still let
        # dashscope-intl users opt in. Imported per-call to match codex_auth.
        from openbiliclaw.network import outbound_httpx_kwargs

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, **outbound_httpx_kwargs()
            ) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                if response.status_code >= 400:
                    snippet = (response.text or "")[:300]
                    logger.warning(
                        "DashScope embedding HTTP %s (model=%s): %s",
                        response.status_code,
                        model,
                        snippet,
                    )
                    return []
                data = response.json()
        except Exception:
            logger.warning(
                "DashScope embedding request failed (model=%s)",
                model,
                exc_info=True,
            )
            return []

        return self._parse_embedding_vector(data)

    @staticmethod
    def _parse_embedding_vector(data: object) -> list[float]:
        if not isinstance(data, dict):
            return []
        # Error payloads: {"code": "...", "message": "..."}
        if data.get("code") and not data.get("output"):
            logger.warning(
                "DashScope embedding error: code=%s message=%s",
                data.get("code"),
                data.get("message"),
            )
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
