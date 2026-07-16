"""OpenAI-compatible embedding client for the dedicated LiteLLM alias."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, ValidationError

EMBEDDING_ALIAS = "obc-embedding"

if TYPE_CHECKING:
    from collections.abc import Sequence


class EmbeddingSettings(BaseModel):
    """Connection settings for the LiteLLM embedding endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(min_length=1)
    api_key: SecretStr
    profile_version: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30, gt=0)


class EmbeddingNamespace(BaseModel):
    """Provider-independent cache namespace for one embedding profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: str = EMBEDDING_ALIAS
    vector_dimension: int = Field(gt=0)
    profile_version: str = Field(min_length=1)

    @property
    def cache_key(self) -> str:
        """Return a stable namespace that changes on alias, dimension, or profile."""

        return f"{self.alias}:{self.vector_dimension}:{self.profile_version}"


class EmbeddingBatch(BaseModel):
    """Ordered vectors plus their required cache namespace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vectors: tuple[tuple[float, ...], ...]
    namespace: EmbeddingNamespace


class _EmbeddingDatum(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    embedding: tuple[float, ...] = Field(min_length=1)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: tuple[_EmbeddingDatum, ...] = Field(min_length=1)


class EmbeddingService:
    """Call LiteLLM embeddings once; proxy policy owns any retry or fallback."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            timeout=settings.timeout_seconds,
            trust_env=False,
        )

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Embed a non-empty ordered batch and validate response dimensions."""

        normalized = tuple(texts)
        if not normalized or any(not text.strip() for text in normalized):
            raise ValueError("embedding requires at least one non-empty text")
        response = await self._client.post(
            "/embeddings",
            headers={"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"},
            json={"input": list(normalized), "model": EMBEDDING_ALIAS},
        )
        response.raise_for_status()
        try:
            payload = TypeAdapter(_EmbeddingResponse).validate_python(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValueError("embedding response is malformed") from exc
        vectors = _ordered_vectors(payload, expected_count=len(normalized))
        dimension = len(vectors[0])
        return EmbeddingBatch(
            vectors=vectors,
            namespace=EmbeddingNamespace(
                vector_dimension=dimension,
                profile_version=self._settings.profile_version,
            ),
        )

    async def aclose(self) -> None:
        """Close the HTTP client only when this service created it."""

        if self._owns_client:
            await self._client.aclose()


def _ordered_vectors(
    response: _EmbeddingResponse, *, expected_count: int
) -> tuple[tuple[float, ...], ...]:
    if len(response.data) != expected_count:
        raise ValueError("embedding response count does not match request")
    ordered = sorted(response.data, key=lambda datum: datum.index)
    if [datum.index for datum in ordered] != list(range(expected_count)):
        raise ValueError("embedding response indices are not contiguous")
    dimension = len(ordered[0].embedding)
    if dimension == 0 or any(len(datum.embedding) != dimension for datum in ordered):
        raise ValueError("embedding response vectors have inconsistent dimensions")
    return tuple(datum.embedding for datum in ordered)
