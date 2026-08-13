"""Embedding access through the same native provider configuration as chat."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from openbiliclaw.ai.providers.embeddings.protocol import EmbeddingBatch, EmbeddingTransportError
from openbiliclaw.ai.providers.verification import UnsupportedCapabilityError

if TYPE_CHECKING:
    from collections.abc import Callable

    from openai import AsyncOpenAI
    from openai.types import CreateEmbeddingResponse

    from openbiliclaw.ai.providers.models.config import ModelInstanceConfig

T = TypeVar("T")


class VaultResolver(Protocol):
    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T: ...


class NativeEmbeddingTransport:
    """Use the native OpenAI provider client's embeddings resource."""

    def __init__(
        self,
        client: AsyncOpenAI,
        config: ModelInstanceConfig,
        *,
        output_dimensions: int,
    ) -> None:
        self._client = client
        self._config = config
        self._output_dimensions = output_dimensions

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        try:
            if self._config.endpoint:
                response: CreateEmbeddingResponse = await self._client.embeddings.create(
                    model=self._config.model_name,
                    input=texts,
                )
            else:
                response = await self._client.embeddings.create(
                    model=self._config.model_name,
                    input=texts,
                    dimensions=self._output_dimensions,
                )
        except Exception as error:
            raise EmbeddingTransportError(retryable=_is_retryable(error)) from error
        return EmbeddingBatch(
            vectors=tuple(tuple(item.embedding) for item in response.data),
            input_tokens=response.usage.prompt_tokens,
        )


def build_embedding_transport(
    config: ModelInstanceConfig,
    vault: VaultResolver,
    *,
    output_dimensions: int,
) -> NativeEmbeddingTransport:
    """Build embeddings from the shared provider config or fail closed."""

    if config.protocol != "openai":
        raise UnsupportedCapabilityError(
            f"{config.provider} embeddings are unsupported by the native provider"
        )

    from pydantic_ai.providers.openai import OpenAIProvider

    def construct(secret: memoryview) -> NativeEmbeddingTransport:
        provider = OpenAIProvider(
            base_url=config.endpoint,
            api_key=secret.tobytes().decode("utf-8"),
        )
        return NativeEmbeddingTransport(
            provider.client,
            config,
            output_dimensions=output_dimensions,
        )

    return vault.resolve(config.secret_ref, construct)


def _is_retryable(error: Exception) -> bool:
    from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

    if isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    return isinstance(error, APIStatusError) and error.status_code >= 500
