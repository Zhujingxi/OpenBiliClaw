"""Focused mapping and compatibility proof for legacy Embedding routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._migration_constants import (
    EMBEDDING_DEFAULT_MODELS,
    OFFICIAL_PATHS,
    PROVIDER_LABELS,
)
from ._migration_inspection import (
    IssueCollector,
    bounded_float_field,
    credential_from_raw,
    exact_bool_field,
    exact_int_field,
    inspect_endpoint,
    legacy_connection_id,
    normalized_ollama_endpoint,
    text_field,
)
from ._migration_types import _EmbeddingSpace
from .types import (
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _embedding_type_and_preset(provider: str) -> tuple[str, str]:
    if provider == "openai":
        return "openai_compatible", "openai"
    if provider in {"openai_compatible", "openrouter"}:
        return "openai_compatible", "custom"
    if provider == "gemini":
        return "gemini_api", ""
    if provider == "dashscope":
        return "dashscope_api", ""
    return "ollama", ""


def _embedding_endpoint(
    provider: str,
    raw_value: object,
    *,
    field: str,
    collector: IssueCollector,
) -> tuple[str, bool]:
    if provider == "openai":
        endpoint = inspect_endpoint(
            raw_value,
            field=field,
            collector=collector,
            official_host="api.openai.com",
            official_paths=OFFICIAL_PATHS,
            canonical_official="https://api.openai.com/v1",
        )
    elif provider == "ollama":
        endpoint = inspect_endpoint(
            raw_value,
            field=field,
            collector=collector,
            default="http://127.0.0.1:11434/v1",
        )
    elif provider == "openrouter":
        endpoint = inspect_endpoint(
            raw_value,
            field=field,
            collector=collector,
            default="https://openrouter.ai/api/v1",
        )
    elif provider == "openai_compatible":
        endpoint = inspect_endpoint(
            raw_value,
            field=field,
            collector=collector,
            required=True,
        )
    else:
        endpoint = inspect_endpoint(raw_value, field=field, collector=collector)
    value = normalized_ollama_endpoint(endpoint.value) if provider == "ollama" else endpoint.value
    return value, endpoint.valid


def map_embedding_provider(
    provider: str,
    endpoint_raw: Mapping[str, object],
    credential_raw: Mapping[str, object],
    env: Mapping[str, str],
    used_ids: set[str],
    collector: IssueCollector,
    *,
    prefix: str,
) -> tuple[EmbeddingProviderConfig, bool]:
    """Map one provider and return its endpoint usability separately."""
    connection_type, preset = _embedding_type_and_preset(provider)
    credential = (
        CredentialConfig()
        if provider == "ollama"
        else credential_from_raw(
            provider,
            credential_raw,
            env,
            prefix=prefix,
            collector=collector,
        )
    )
    base_url_raw = endpoint_raw.get("base_url", "")
    if base_url_raw == "" and endpoint_raw is not credential_raw:
        base_url_raw = credential_raw.get("base_url", "")
    base_url, endpoint_valid = _embedding_endpoint(
        provider,
        base_url_raw,
        field=f"{prefix}.base_url",
        collector=collector,
    )
    return (
        EmbeddingProviderConfig(
            id=legacy_connection_id("embedding", provider, used_ids),
            name=PROVIDER_LABELS[provider],
            type=connection_type,
            preset=preset,
            base_url=base_url,
            credential=credential,
        ),
        endpoint_valid,
    )


def map_embedding_settings(
    raw: Mapping[str, object],
    provider: str,
    collector: IssueCollector,
) -> EmbeddingModelSettings:
    """Map the one shared vector-space settings record with exact types."""
    model = text_field(
        raw,
        "model",
        field="llm.embedding.model",
        collector=collector,
        default=EMBEDDING_DEFAULT_MODELS.get(provider, ""),
    ).value or EMBEDDING_DEFAULT_MODELS.get(provider, "")
    output = exact_int_field(
        raw,
        "output_dimensionality",
        field="llm.embedding.output_dimensionality",
        collector=collector,
        default=1024,
        minimum=0,
        maximum=None,
        reason="embedding_dimension_is_invalid",
    )
    threshold = bounded_float_field(
        raw,
        "similarity_threshold",
        field="llm.embedding.similarity_threshold",
        collector=collector,
        default=0.82,
        minimum=0.0,
        maximum=1.0,
        reason="embedding_similarity_threshold_is_invalid",
    )
    multimodal = exact_bool_field(
        raw,
        "multimodal_enabled",
        field="llm.embedding.multimodal_enabled",
        collector=collector,
        default=False,
        reason="embedding_multimodal_flag_is_invalid",
    )
    return EmbeddingModelSettings(
        model=model,
        output_dimensionality=output,
        similarity_threshold=threshold,
        multimodal_enabled=multimodal,
    )


def embedding_space(provider: str) -> _EmbeddingSpace:
    """Describe a fallback's actual default model and dimension behavior."""
    model = EMBEDDING_DEFAULT_MODELS[provider]
    if provider == "ollama":
        return _EmbeddingSpace(provider=provider, model=model, fixed_output_dimensionality=1024)
    if provider == "openai":
        return _EmbeddingSpace(
            provider=provider,
            model=model,
            output_dimensionality_configurable=model.startswith("text-embedding-3-"),
        )
    if provider == "gemini":
        return _EmbeddingSpace(
            provider=provider,
            model=model,
            output_dimensionality_configurable=True,
            multimodal_capable="gemini-embedding-2" in model.lower(),
        )
    if provider == "dashscope":
        lowered = model.lower()
        return _EmbeddingSpace(
            provider=provider,
            model=model,
            output_dimensionality_configurable="qwen3-vl-embedding" in lowered,
            multimodal_capable=any(
                marker in lowered
                for marker in (
                    "qwen3-vl-embedding",
                    "tongyi-embedding-vision",
                    "multimodal-embedding",
                )
            ),
        )
    return _EmbeddingSpace(provider=provider, model=model)


def embedding_provider_usable(
    provider: EmbeddingProviderConfig, endpoint_valid: bool = True
) -> bool:
    """Return whether a fallback has enough safe connection configuration."""
    if not endpoint_valid:
        return False
    if provider.type in {"gemini_api", "dashscope_api"}:
        return provider.credential.source != "none"
    if not provider.base_url:
        return False
    if provider.type == "ollama":
        return True
    return provider.credential.source != "none"


def embedding_space_compatible(
    provider: EmbeddingProviderConfig,
    space: _EmbeddingSpace,
    settings: EmbeddingModelSettings,
    *,
    endpoint_valid: bool = True,
) -> bool:
    """Prove exact effective model, dimensions, and multimodal capability."""
    if not embedding_provider_usable(provider, endpoint_valid):
        return False
    if not space.model or space.model != settings.model:
        return False
    if space.output_dimensionality_configurable:
        dimension_matches = settings.output_dimensionality >= 0
    elif space.fixed_output_dimensionality is not None:
        dimension_matches = space.fixed_output_dimensionality == settings.output_dimensionality
    else:
        dimension_matches = False
    if not dimension_matches:
        return False
    return not settings.multimodal_enabled or space.multimodal_capable


__all__ = [
    "embedding_provider_usable",
    "embedding_space",
    "embedding_space_compatible",
    "map_embedding_provider",
    "map_embedding_settings",
]
