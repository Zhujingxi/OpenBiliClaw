"""Ordered embedding routing for one immutable shared vector space."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from .base import LLMFallbackError, LLMProviderError, classify_llm_failure_kind
from .embedding import _coerce_embedding_vector
from .route import CircuitTable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openbiliclaw.model_config import EmbeddingModelSettings

    from .connection_factory import SupportsEmbedding


# Repository-owned 1x1 PNG used only for exact image-capability probes.  Keeping
# the bytes fixed makes every provider probe deterministic and avoids reading a
# user image, external URL, or mutable fixture.
FIXED_IMAGE_PROBE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdacd\xf8\x0f\x00\x01\x05\x01"
    b"\x01'\x18\xe3f\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PROBE_TEXT = "openbiliclaw embedding provider probe"

EmbeddingFailureKind: TypeAlias = Literal[
    "rate_limited",
    "auth_failed",
    "model_not_found",
    "timeout",
    "connection",
    "server_error",
    "invalid_response",
    "moderation",
    "config_error",
    "empty_vector",
    "invalid_vector",
    "image_capability",
    "provider_error",
]
_CLASSIFIED_FAILURE_KINDS = frozenset(
    {
        "rate_limited",
        "auth_failed",
        "model_not_found",
        "timeout",
        "connection",
        "server_error",
        "invalid_response",
        "moderation",
    }
)
_SAFE_SUMMARIES: dict[EmbeddingFailureKind, str] = {
    "rate_limited": "The embedding provider is rate limited or out of quota.",
    "auth_failed": "The embedding provider authentication failed.",
    "model_not_found": "The configured embedding model was not found.",
    "timeout": "The embedding provider timed out.",
    "connection": "The embedding provider could not reach its endpoint.",
    "server_error": "The embedding provider returned a server error.",
    "invalid_response": "The embedding provider returned an invalid response.",
    "moderation": "The embedding provider declined the request.",
    "config_error": "The provider vector dimension is incompatible with shared settings.",
    "empty_vector": "The embedding provider returned an empty vector.",
    "invalid_vector": "The embedding provider returned a non-numeric or non-finite vector.",
    "image_capability": "The provider cannot serve the enabled image embedding capability.",
    "provider_error": "The embedding provider could not complete the request.",
}


@dataclass(frozen=True)
class EmbeddingRouteAttempt:
    """Secret-safe summary of one failed provider attempt."""

    provider_id: str
    connection_type: str
    preset: str
    route_position: int
    failure_kind: EmbeddingFailureKind
    summary: str

    @classmethod
    def safe(
        cls,
        provider: SupportsEmbedding,
        position: int,
        failure_kind: EmbeddingFailureKind,
    ) -> EmbeddingRouteAttempt:
        """Build a fixed-text attempt without retaining provider output."""
        return cls(
            provider_id=provider.name,
            connection_type=provider.connection_type,
            preset=provider.preset,
            route_position=position,
            failure_kind=failure_kind,
            summary=_SAFE_SUMMARIES[failure_kind],
        )


class EmbeddingRouteExhaustedError(LLMFallbackError):
    """Raised with safe attempts when no embedding provider succeeds."""

    def __init__(self, attempts: Sequence[EmbeddingRouteAttempt]) -> None:
        self.attempts = tuple(attempts)
        if self.attempts:
            rendered = ", ".join(
                f"{attempt.provider_id}:{attempt.failure_kind}" for attempt in self.attempts
            )
            message = f"All configured embedding providers failed ({rendered})."
        else:
            message = "No embedding provider was available for this request."
        super().__init__(message)


@dataclass(frozen=True)
class EmbeddingProbeResult:
    """Observed capability facts from one exact provider probe.

    The result intentionally reports dimensions and whether the fixed image
    check ran.  It makes no assertion about which remote model weights were
    actually loaded; that fact cannot be proven from a provider response.
    """

    provider_id: str
    observed_dimension: int
    image_probe_performed: bool


class OrderedEmbeddingRoute:
    """Try providers in array order under one shared embedding model space."""

    def __init__(
        self,
        providers: Sequence[SupportsEmbedding],
        settings: EmbeddingModelSettings,
        revision: str,
        circuits: CircuitTable | None = None,
    ) -> None:
        self.providers = tuple(providers)
        self.settings = settings
        self.revision = revision
        self.circuits = circuits or CircuitTable()
        for provider in self.providers:
            if provider.settings is not settings:
                raise ValueError(
                    "every embedding adapter must use the route shared settings object"
                )

    @property
    def supports_image_embedding(self) -> bool:
        """Return whether at least one ordered peer can serve image vectors."""
        return any(self._supports_image(provider) for provider in self.providers)

    async def embed(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float]:
        """Return the first valid text vector in exact provider order.

        ``model`` is a Task 8 staging shim for ``EmbeddingService``'s legacy
        provider protocol.  It may repeat the shared model but cannot override
        it; native route callers omit it.
        """
        self._require_shared_model(model)
        attempts: list[EmbeddingRouteAttempt] = []
        for position, provider in enumerate(self.providers):
            if self._append_open_circuit(provider, position, attempts):
                continue
            if self.settings.multimodal_enabled and not self._supports_image(provider):
                attempts.append(EmbeddingRouteAttempt.safe(provider, position, "image_capability"))
                continue
            vector = await self._attempt(
                provider,
                position,
                operation="text",
                payload=text,
                mime_type="",
                attempts=attempts,
                close_circuit=True,
            )
            if vector is not None:
                return vector
        raise EmbeddingRouteExhaustedError(attempts)

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        model: str | None = None,
    ) -> list[float]:
        """Return the first valid image vector, or ``[]`` when disabled."""
        self._require_shared_model(model)
        if not self.settings.multimodal_enabled or not image_bytes:
            return []
        attempts: list[EmbeddingRouteAttempt] = []
        for position, provider in enumerate(self.providers):
            if self._append_open_circuit(provider, position, attempts):
                continue
            if not self._supports_image(provider):
                attempts.append(EmbeddingRouteAttempt.safe(provider, position, "image_capability"))
                continue
            vector = await self._attempt(
                provider,
                position,
                operation="image",
                payload=image_bytes,
                mime_type=mime_type or "image/jpeg",
                attempts=attempts,
                close_circuit=True,
            )
            if vector is not None:
                return vector
        raise EmbeddingRouteExhaustedError(attempts)

    async def probe_provider(self, provider_id: str) -> EmbeddingProbeResult:
        """Probe exactly one provider, bypassing its current circuit and caches."""
        provider, position = self._find_provider(provider_id)
        attempts: list[EmbeddingRouteAttempt] = []
        if self.settings.multimodal_enabled and not self._supports_image(provider):
            attempts.append(EmbeddingRouteAttempt.safe(provider, position, "image_capability"))
            raise EmbeddingRouteExhaustedError(attempts)

        text_vector = await self._attempt(
            provider,
            position,
            operation="text",
            payload=_PROBE_TEXT,
            mime_type="",
            attempts=attempts,
            close_circuit=False,
        )
        if text_vector is None:
            raise EmbeddingRouteExhaustedError(attempts)

        image_probe_performed = False
        if self.settings.multimodal_enabled:
            image_vector = await self._attempt(
                provider,
                position,
                operation="image",
                payload=FIXED_IMAGE_PROBE_PNG,
                mime_type="image/png",
                attempts=attempts,
                close_circuit=False,
            )
            if image_vector is None:
                raise EmbeddingRouteExhaustedError(attempts)
            image_probe_performed = True

        self.circuits.record_success(provider.name, self.revision)
        return EmbeddingProbeResult(
            provider_id=provider.name,
            observed_dimension=len(text_vector),
            image_probe_performed=image_probe_performed,
        )

    async def _attempt(
        self,
        provider: SupportsEmbedding,
        position: int,
        *,
        operation: Literal["text", "image"],
        payload: str | bytes,
        mime_type: str,
        attempts: list[EmbeddingRouteAttempt],
        close_circuit: bool,
    ) -> list[float] | None:
        try:
            if operation == "text":
                raw_vector = await provider.embed(cast("str", payload))
            else:
                raw_vector = await provider.embed_image(
                    cast("bytes", payload),
                    mime_type=mime_type,
                )
        except Exception as exc:
            failure_kind = self._provider_failure_kind(exc)
            attempts.append(EmbeddingRouteAttempt.safe(provider, position, failure_kind))
            self.circuits.record_failure(
                provider.name,
                self.revision,
                failure_kind,
                exc,
            )
            return None

        vector, validation_kind = self._validate_vector(raw_vector)
        if validation_kind is not None:
            attempts.append(EmbeddingRouteAttempt.safe(provider, position, validation_kind))
            if validation_kind == "config_error":
                self.circuits.record_failure(
                    provider.name,
                    self.revision,
                    validation_kind,
                    LLMProviderError("embedding vector dimension mismatch"),
                )
            return None
        if close_circuit:
            self.circuits.record_success(provider.name, self.revision)
        return vector

    def _validate_vector(
        self,
        value: object,
    ) -> tuple[list[float] | None, EmbeddingFailureKind | None]:
        if isinstance(value, list) and not value:
            return None, "empty_vector"
        vector = _coerce_embedding_vector(value)
        if vector is None:
            return None, "invalid_vector"
        if not vector:
            return None, "empty_vector"
        expected = self.settings.output_dimensionality
        if expected != 0 and len(vector) != expected:
            return None, "config_error"
        return vector, None

    def _append_open_circuit(
        self,
        provider: SupportsEmbedding,
        position: int,
        attempts: list[EmbeddingRouteAttempt],
    ) -> bool:
        if not self.circuits.should_skip(provider.name, self.revision):
            return False
        state = self.circuits.state_for(provider.name, self.revision)
        if state is not None:
            attempts.append(
                EmbeddingRouteAttempt.safe(
                    provider,
                    position,
                    cast("EmbeddingFailureKind", state.failure_kind),
                )
            )
        return True

    @staticmethod
    def _provider_failure_kind(exc: BaseException) -> EmbeddingFailureKind:
        kind = classify_llm_failure_kind(exc)
        if kind in _CLASSIFIED_FAILURE_KINDS:
            return cast("EmbeddingFailureKind", kind)
        return "provider_error"

    @staticmethod
    def _supports_image(provider: SupportsEmbedding) -> bool:
        try:
            return bool(provider.supports_image_embedding) and callable(
                getattr(provider, "embed_image", None)
            )
        except Exception:
            return False

    def _require_shared_model(self, model: str | None) -> None:
        if model is not None and model != self.settings.model:
            raise ValueError("embedding model override is not allowed")

    def _find_provider(self, provider_id: str) -> tuple[SupportsEmbedding, int]:
        for position, provider in enumerate(self.providers):
            if provider.name == provider_id:
                return provider, position
        raise KeyError(f"unknown embedding provider: {provider_id}")


__all__ = [
    "FIXED_IMAGE_PROBE_PNG",
    "EmbeddingFailureKind",
    "EmbeddingProbeResult",
    "EmbeddingRouteAttempt",
    "EmbeddingRouteExhaustedError",
    "OrderedEmbeddingRoute",
]
