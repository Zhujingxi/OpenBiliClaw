"""Closed, all-or-nothing application of legacy migration decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ._migration_embedding import embedding_space_compatible
from ._migration_types import (
    LegacyMigrationResult,
    MigrationResolution,
    MigrationResolutionError,
    _EmbeddingProviderState,
)
from .registry import connection_type_registry
from .types import ChatConnection, EmbeddingModelSettings, EmbeddingProviderConfig, ModelConfig
from .validation import validate_model_config


def _resolution_error() -> MigrationResolutionError:
    return MigrationResolutionError("migration resolutions are incomplete or invalid")


def _valid_embedding_settings(value: object) -> bool:
    return (
        isinstance(value, EmbeddingModelSettings)
        and bool(value.model.strip())
        and type(value.output_dimensionality) is int
        and value.output_dimensionality >= 0
        and isinstance(value.similarity_threshold, int | float)
        and not isinstance(value.similarity_threshold, bool)
        and 0.0 <= float(value.similarity_threshold) <= 1.0
        and type(value.multimodal_enabled) is bool
    )


def _insert_chat_connections(
    existing: tuple[ChatConnection, ...],
    additions: list[tuple[int, ChatConnection]],
) -> tuple[ChatConnection, ...]:
    final_count = len(existing) + len(additions)
    if not 1 <= final_count <= 10:
        raise _resolution_error()
    positions = [position for position, _connection in additions]
    if len(positions) != len(set(positions)):
        raise _resolution_error()
    if any(position < 1 or position > final_count for position in positions):
        raise _resolution_error()

    by_position = {position: connection for position, connection in additions}
    remaining = iter(existing)
    resolved: list[ChatConnection] = []
    for position in range(1, final_count + 1):
        connection = by_position.get(position)
        resolved.append(connection if connection is not None else next(remaining))
    return tuple(resolved)


def _validate_final_model(config: ModelConfig) -> None:
    issues = validate_model_config(config, connection_type_registry())
    if any(issue.severity == "blocking" for issue in issues):
        raise _resolution_error()


def apply_migration_resolutions(
    result: LegacyMigrationResult,
    choices: Mapping[str, MigrationResolution],
) -> ModelConfig:
    """Apply every blocking migration decision and return only a valid model.

    Backup acknowledgements remain metadata for the later transactional
    persistence service.  This function performs no filesystem operation and
    returns no partially resolved candidate.
    """
    if not isinstance(result, LegacyMigrationResult) or not isinstance(choices, Mapping):
        raise _resolution_error()

    required = tuple(issue for issue in result.report.issues if issue.severity == "blocking")
    required_ids = {issue.id for issue in required}
    if set(choices) != required_ids:
        raise _resolution_error()

    pending = {item.issue_id: item for item in result._pending}
    chat_additions: list[tuple[int, ChatConnection]] = []
    chat_removals: set[str] = set()
    embedding_removals: set[str] = set()
    embedding_addition: (
        tuple[EmbeddingProviderConfig, _EmbeddingProviderState, EmbeddingModelSettings] | None
    ) = None

    for issue in required:
        resolution = choices.get(issue.id)
        if not isinstance(resolution, MigrationResolution):
            raise _resolution_error()
        if resolution.action not in issue.allowed_actions or resolution.action == "cancel":
            raise _resolution_error()

        value = pending.get(issue.id)
        if resolution.action == "add_to_chat_route":
            if type(resolution.position) is not int or resolution.embedding_settings is not None:
                raise _resolution_error()
            if value is None or value.chat_connection is None:
                raise _resolution_error()
            chat_additions.append((resolution.position, value.chat_connection))
            continue

        if resolution.action == "apply_shared_embedding_settings":
            if resolution.position is not None or not _valid_embedding_settings(
                resolution.embedding_settings
            ):
                raise _resolution_error()
            if (
                value is None
                or value.embedding_provider is None
                or value.embedding_state is None
                or embedding_addition is not None
                or resolution.embedding_settings is None
                or value.embedding_state.provider_id != value.embedding_provider.id
            ):
                raise _resolution_error()
            embedding_addition = (
                value.embedding_provider,
                value.embedding_state,
                resolution.embedding_settings,
            )
            continue

        if resolution.position is not None or resolution.embedding_settings is not None:
            raise _resolution_error()
        if (
            resolution.action == "confirm_remove_after_backup"
            and value is not None
            and value.remove_chat_connection_id
        ):
            chat_removals.add(value.remove_chat_connection_id)
        if (
            resolution.action == "confirm_remove_after_backup"
            and value is not None
            and value.remove_embedding_provider_id
        ):
            embedding_removals.add(value.remove_embedding_provider_id)

    retained_chat = tuple(
        connection
        for connection in result.models.chat.connections
        if connection.id not in chat_removals
    )
    chat_connections = _insert_chat_connections(retained_chat, chat_additions)

    retained_embedding = tuple(
        provider
        for provider in result.models.embedding.providers
        if provider.id not in embedding_removals
    )
    embedding_providers = retained_embedding
    embedding_settings = result.models.embedding.settings
    if embedding_addition is not None:
        provider, pending_state, embedding_settings = embedding_addition
        embedding_providers = (*embedding_providers, provider)
        if len(embedding_providers) > 10:
            raise _resolution_error()

        states_by_id: dict[str, _EmbeddingProviderState] = {}
        for state in (*result._embedding_states, pending_state):
            if not state.provider_id or state.provider_id in states_by_id:
                raise _resolution_error()
            states_by_id[state.provider_id] = state
        for final_provider in embedding_providers:
            final_state = states_by_id.get(final_provider.id)
            if final_state is None or not embedding_space_compatible(
                final_provider,
                final_state.space,
                embedding_settings,
                endpoint_valid=final_state.endpoint_valid,
            ):
                raise _resolution_error()

    embedding = replace(
        result.models.embedding,
        enabled=bool(embedding_providers),
        settings=embedding_settings,
        providers=embedding_providers,
    )

    all_ids = [connection.id for connection in chat_connections] + [
        provider.id for provider in embedding.providers
    ]
    if any(not item.strip() for item in all_ids) or len(all_ids) != len(set(all_ids)):
        raise _resolution_error()

    resolved = replace(
        result.models,
        chat=replace(result.models.chat, connections=chat_connections),
        embedding=embedding,
    )
    _validate_final_model(resolved)
    return resolved


__all__ = ["apply_migration_resolutions"]
