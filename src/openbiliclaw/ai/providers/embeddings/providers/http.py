"""Typed HTTP transports for the configured OpenAI, Google, and Ollama embeddings."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from openbiliclaw.ai.providers.embeddings.protocol import EmbeddingBatch, EmbeddingTransportError
from openbiliclaw.core._pydantic import StrictBaseModel

if TYPE_CHECKING:
    import httpx

    from openbiliclaw.ai.providers.models.factory import VaultResolver


class EmbeddingProviderKind(StrEnum):
    OPENAI = "openai"
    GOOGLE = "google"
    OLLAMA = "ollama"


class EmbeddingTransportConfig(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: EmbeddingProviderKind
    model: str = Field(min_length=1)
    endpoint: str | None = None
    secret_ref: str | None = Field(default=None, pattern=r"^cred_[0-9a-f]{32}$")
    output_dimensions: int = Field(gt=0)
    # Some OpenAI models (e.g. text-embedding-ada-002) reject the dimensions
    # parameter; set False for those. Output length is still validated.
    send_dimensions: bool = True


class _OpenAIItem(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    embedding: tuple[float, ...]


class _OpenAIUsage(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_tokens: int = 0


class _OpenAIResponse(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    data: tuple[_OpenAIItem, ...]
    usage: _OpenAIUsage = _OpenAIUsage()


class _GoogleItem(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    values: tuple[float, ...]


class _GoogleUsage(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_token_count: int = Field(default=0, alias="promptTokenCount")


class _GoogleResponse(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    embeddings: tuple[_GoogleItem, ...]
    usage_metadata: _GoogleUsage = Field(default=_GoogleUsage(), alias="usageMetadata")


class _OllamaResponse(StrictBaseModel):
    model_config = ConfigDict(extra="ignore")
    embeddings: tuple[tuple[float, ...], ...]
    prompt_eval_count: int = 0


class OpenAIEmbeddingTransport:
    def __init__(self, client: httpx.AsyncClient, config: EmbeddingTransportConfig) -> None:
        self._client = client
        self._config = config

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        endpoint = self._config.endpoint or "https://api.openai.com/v1/embeddings"
        payload: dict[str, object] = {"model": self._config.model, "input": texts}
        if self._config.send_dimensions:
            payload["dimensions"] = self._config.output_dimensions
        response = await self._client.post(endpoint, json=payload)
        _raise_for_status(response)
        parsed = _OpenAIResponse.model_validate_json(response.content)
        return EmbeddingBatch(
            vectors=tuple(item.embedding for item in parsed.data),
            input_tokens=parsed.usage.prompt_tokens,
        )


class GoogleEmbeddingTransport:
    def __init__(self, client: httpx.AsyncClient, config: EmbeddingTransportConfig) -> None:
        self._client = client
        self._config = config

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        endpoint = self._config.endpoint or (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._config.model}:batchEmbedContents"
        )
        response = await self._client.post(
            endpoint,
            json={
                "requests": tuple(
                    {
                        "model": f"models/{self._config.model}",
                        "content": {"parts": ({"text": text},)},
                        "outputDimensionality": self._config.output_dimensions,
                    }
                    for text in texts
                )
            },
        )
        _raise_for_status(response)
        parsed = _GoogleResponse.model_validate_json(response.content)
        return EmbeddingBatch(
            vectors=tuple(item.values for item in parsed.embeddings),
            input_tokens=parsed.usage_metadata.prompt_token_count,
        )


class OllamaEmbeddingTransport:
    def __init__(self, client: httpx.AsyncClient, config: EmbeddingTransportConfig) -> None:
        self._client = client
        self._config = config

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        endpoint = self._config.endpoint or "http://127.0.0.1:11434/api/embed"
        response = await self._client.post(
            endpoint,
            json={"model": self._config.model, "input": texts},
        )
        _raise_for_status(response)
        parsed = _OllamaResponse.model_validate_json(response.content)
        return EmbeddingBatch(parsed.embeddings, parsed.prompt_eval_count)


def build_embedding_transport(
    config: EmbeddingTransportConfig,
    vault: VaultResolver,
    client: httpx.AsyncClient,
) -> OpenAIEmbeddingTransport | GoogleEmbeddingTransport | OllamaEmbeddingTransport:
    """Resolve credentials only while configuring the trusted HTTP client."""

    if config.provider is not EmbeddingProviderKind.OLLAMA:
        if config.secret_ref is None:
            raise ValueError(f"{config.provider.value} requires a credential reference")

        def configure(secret: memoryview) -> None:
            value = secret.tobytes().decode("utf-8")
            if config.provider is EmbeddingProviderKind.GOOGLE:
                client.headers["x-goog-api-key"] = value
            else:
                client.headers["authorization"] = f"Bearer {value}"

        vault.resolve(config.secret_ref, configure)
    if config.provider is EmbeddingProviderKind.OPENAI:
        return OpenAIEmbeddingTransport(client, config)
    if config.provider is EmbeddingProviderKind.GOOGLE:
        return GoogleEmbeddingTransport(client, config)
    return OllamaEmbeddingTransport(client, config)


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        raise EmbeddingTransportError(
            retryable=response.status_code == 429 or response.status_code >= 500
        )
