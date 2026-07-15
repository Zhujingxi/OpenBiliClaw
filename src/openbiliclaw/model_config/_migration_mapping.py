"""Compact orchestration for deterministic legacy model migration."""

from __future__ import annotations

from collections.abc import Mapping

from ._migration_chat import MappedChat, map_chat_connection
from ._migration_constants import (
    CHAT_PROVIDER_ORDER,
    EMBEDDING_FIELDS,
    EMBEDDING_PROVIDERS,
    KNOWN_LLM_FIELDS,
    MODULE_NAMES,
    PROVIDER_FIELDS,
)
from ._migration_embedding import (
    active_embedding_space,
    embedding_provider_usable,
    embedding_space,
    embedding_space_compatible,
    map_embedding_provider,
    map_embedding_settings,
)
from ._migration_inspection import (
    IssueCollector,
    credential_from_raw,
    exact_bool_field,
    exact_int_field,
    inspect_credential_from_raw,
    raw_table,
    safe_identifier,
    text_field,
    unknown_credential_configured,
    value_configured,
)
from ._migration_types import (
    EMBEDDING_MISMATCH_ACTIONS,
    MODULE_OVERRIDE_ACTIONS,
    UNROUTED_ACTIONS,
    LegacyMigrationResult,
    MigrationReport,
    _EmbeddingProviderState,
    _PendingValue,
)
from .types import (
    ChatConnection,
    ChatRouteConfig,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
)


def _report_unknown_fields(
    raw: Mapping[str, object],
    *,
    allowed: frozenset[str],
    prefix: str,
    provider: str,
    collector: IssueCollector,
) -> None:
    for name in sorted(set(raw) - allowed):
        value = raw[name]
        collector.add(
            "unknown_legacy_field",
            f"{prefix}.{name}",
            provider=provider,
            credential_configured=(
                value_configured(value)
                if any(marker in name.lower() for marker in ("key", "token", "secret"))
                else False
            ),
            reason="legacy_field_has_no_safe_mapping",
        )


def _provider_name(
    raw: Mapping[str, object],
    name: str,
    *,
    field: str,
    collector: IssueCollector,
) -> tuple[str, bool]:
    value = text_field(
        raw,
        name,
        field=field,
        collector=collector,
        reason="legacy_provider_name_must_be_string",
    )
    return value.value.lower(), value.valid


def _known_tables(
    raw: Mapping[str, object],
    collector: IssueCollector,
) -> tuple[dict[str, dict[str, object]], dict[str, object], dict[str, dict[str, object]]]:
    provider_tables = {
        provider: raw_table(
            raw,
            provider,
            field=f"llm.{provider}",
            collector=collector,
        )
        for provider in CHAT_PROVIDER_ORDER
    }
    for provider in CHAT_PROVIDER_ORDER:
        _report_unknown_fields(
            provider_tables[provider],
            allowed=PROVIDER_FIELDS[provider],
            prefix=f"llm.{provider}",
            provider=provider,
            collector=collector,
        )

    embedding_raw = raw_table(
        raw,
        "embedding",
        field="llm.embedding",
        collector=collector,
    )
    module_tables = {
        name: raw_table(raw, name, field=f"llm.{name}", collector=collector)
        for name in MODULE_NAMES
    }
    return provider_tables, embedding_raw, module_tables


def _chat_route_names(
    raw: Mapping[str, object],
    collector: IssueCollector,
) -> list[str]:
    route_names: list[str] = []
    for field_name in ("default_provider", "fallback_provider"):
        provider, valid = _provider_name(
            raw,
            field_name,
            field=f"llm.{field_name}",
            collector=collector,
        )
        raw_name = raw.get(field_name, "")
        if not provider:
            if field_name == "default_provider" and valid:
                collector.add(
                    "unknown_provider",
                    "llm.default_provider",
                    provider=safe_identifier(raw_name),
                    reason="legacy_default_provider_is_missing",
                )
            continue
        if isinstance(raw_name, str) and raw_name.strip() != provider:
            collector.add(
                "translated_legacy_value",
                f"llm.{field_name}",
                provider=provider,
                reason="provider_identifier_was_normalized",
                severity="warning",
                allowed_actions=(),
            )
        if provider not in CHAT_PROVIDER_ORDER:
            collector.add(
                "unknown_provider",
                f"llm.{field_name}",
                provider=safe_identifier(raw_name),
                credential_configured=unknown_credential_configured(raw.get(provider)),
                reason="legacy_chat_provider_has_no_safe_mapping",
            )
            continue
        if provider not in route_names:
            route_names.append(provider)
    return route_names


def _map_chat_routes(
    raw: Mapping[str, object],
    provider_tables: Mapping[str, Mapping[str, object]],
    environment: Mapping[str, str],
    used_ids: set[str],
    collector: IssueCollector,
    pending: list[_PendingValue],
) -> tuple[ChatConnection, ...]:
    route_names = _chat_route_names(raw, collector)
    mapped_chat: dict[str, MappedChat] = {}

    def map_chat(provider: str) -> MappedChat:
        mapped = mapped_chat.get(provider)
        if mapped is None:
            mapped = map_chat_connection(
                provider,
                provider_tables[provider],
                environment,
                used_ids,
                collector,
            )
            mapped_chat[provider] = mapped
        return mapped

    active_mapped = [map_chat(provider) for provider in route_names]
    chat_connections = tuple(mapped.connection for mapped in active_mapped)
    for mapped in active_mapped:
        for issue_id in mapped.removal_issue_ids:
            pending.append(
                _PendingValue(
                    issue_id=issue_id,
                    remove_chat_connection_id=mapped.connection.id,
                )
            )

    for provider in CHAT_PROVIDER_ORDER:
        if provider in route_names:
            continue
        provider_raw = provider_tables[provider]
        credential = credential_from_raw(
            provider,
            provider_raw,
            environment,
            prefix=f"llm.{provider}",
            collector=collector,
        )
        auth_mode = (
            text_field(
                provider_raw,
                "auth_mode",
                field=f"llm.{provider}.auth_mode",
                collector=collector,
            ).value.lower()
            if provider == "openai"
            else ""
        )
        configured = credential.source != "none" or auth_mode == "codex_oauth"
        if not configured:
            continue
        candidate = map_chat(provider).connection
        issue = collector.add(
            "unrouted_credential",
            f"llm.{provider}.api_key",
            provider=provider,
            credential_configured=True,
            reason="configured_credential_is_not_in_explicit_chat_route",
            allowed_actions=UNROUTED_ACTIONS,
        )
        pending.append(_PendingValue(issue_id=issue.id, chat_connection=candidate))
    return chat_connections


def _report_module_overrides(
    module_tables: Mapping[str, Mapping[str, object]],
    collector: IssueCollector,
) -> None:
    for module_name in MODULE_NAMES:
        module_raw = module_tables[module_name]
        provider, _valid = _provider_name(
            module_raw,
            "provider",
            field=f"llm.{module_name}.provider",
            collector=collector,
        )
        model = text_field(
            module_raw,
            "model",
            field=f"llm.{module_name}.model",
            collector=collector,
        ).value
        if provider or model:
            collector.add(
                "module_override_removed",
                f"llm.{module_name}",
                provider=provider,
                reason="module_override_must_use_global_route",
                allowed_actions=MODULE_OVERRIDE_ACTIONS,
            )
        _report_unknown_fields(
            module_raw,
            allowed=frozenset({"provider", "model"}),
            prefix=f"llm.{module_name}",
            provider=provider,
            collector=collector,
        )


def _map_embedding_route(
    raw: Mapping[str, object],
    embedding_raw: Mapping[str, object],
    provider_tables: Mapping[str, Mapping[str, object]],
    environment: Mapping[str, str],
    used_ids: set[str],
    collector: IssueCollector,
    pending: list[_PendingValue],
    embedding_states: list[_EmbeddingProviderState],
) -> EmbeddingRouteConfig:
    embedding_name, _valid = _provider_name(
        embedding_raw,
        "provider",
        field="llm.embedding.provider",
        collector=collector,
    )
    _report_unknown_fields(
        embedding_raw,
        allowed=EMBEDDING_FIELDS,
        prefix="llm.embedding",
        provider=embedding_name,
        collector=collector,
    )
    settings = map_embedding_settings(embedding_raw, embedding_name, collector)
    fallback_enabled = exact_bool_field(
        embedding_raw,
        "fallback_enabled",
        field="llm.embedding.fallback_enabled",
        collector=collector,
        default=False,
        reason="embedding_fallback_enabled_must_be_boolean",
    )
    providers: list[EmbeddingProviderConfig] = []

    embedding_credential = inspect_credential_from_raw(
        embedding_name,
        embedding_raw,
        environment,
        prefix="llm.embedding",
        collector=collector,
    )
    if embedding_credential.credential.source == "inline" and embedding_name in {"", "ollama"}:
        collector.add(
            "unused_credential",
            "llm.embedding.api_key",
            provider=embedding_name,
            credential_configured=True,
            reason="embedding_credential_has_no_remote_provider",
        )

    if embedding_name:
        if embedding_name not in EMBEDDING_PROVIDERS:
            collector.add(
                "unknown_provider",
                "llm.embedding.provider",
                provider=embedding_name,
                credential_configured=embedding_credential.configured,
                reason="legacy_embedding_provider_has_no_safe_mapping",
            )
        else:
            base_value = embedding_raw.get("base_url", "")
            borrow_primary = (
                fallback_enabled
                and embedding_credential.valid
                and embedding_credential.credential.source == "none"
                and isinstance(base_value, str)
                and not base_value.strip()
            )
            primary_credentials = (
                provider_tables.get(embedding_name, {}) if borrow_primary else embedding_raw
            )
            primary_mapped = map_embedding_provider(
                embedding_name,
                embedding_raw,
                primary_credentials,
                environment,
                used_ids,
                collector,
                endpoint_prefix="llm.embedding",
                credential_prefix=f"llm.{embedding_name}" if borrow_primary else None,
                inspected_credential=None if borrow_primary else embedding_credential,
            )
            primary_provider = primary_mapped.provider
            providers.append(primary_provider)
            embedding_states.append(
                _EmbeddingProviderState(
                    provider_id=primary_provider.id,
                    space=active_embedding_space(embedding_name, settings),
                    endpoint_valid=primary_mapped.endpoint_valid,
                )
            )
            if not embedding_provider_usable(primary_provider, primary_mapped.endpoint_valid):
                removal_issue_ids: list[str] = []
                credential_inspection = primary_mapped.credential_inspection
                if (
                    primary_provider.type != "ollama"
                    and primary_provider.credential.source == "none"
                ):
                    credential_issue_id = credential_inspection.issue_id
                    if not credential_issue_id:
                        credential_issue_id = collector.add(
                            "invalid_legacy_value",
                            credential_inspection.source_field,
                            provider=embedding_name,
                            credential_configured=False,
                            reason="configured_embedding_provider_has_no_usable_configuration",
                        ).id
                    removal_issue_ids.append(credential_issue_id)
                endpoint_issue_id = primary_mapped.endpoint_inspection.issue_id
                if not primary_mapped.endpoint_valid and endpoint_issue_id:
                    removal_issue_ids.append(endpoint_issue_id)
                if not removal_issue_ids:
                    removal_issue_ids.append(
                        collector.add(
                            "invalid_legacy_value",
                            primary_mapped.endpoint_inspection.source_field,
                            provider=embedding_name,
                            credential_configured=False,
                            reason="configured_embedding_provider_has_no_usable_configuration",
                        ).id
                    )
                for issue_id in dict.fromkeys(removal_issue_ids):
                    pending.append(
                        _PendingValue(
                            issue_id=issue_id,
                            remove_embedding_provider_id=primary_provider.id,
                        )
                    )

    fallback_name, _fallback_valid = _provider_name(
        embedding_raw,
        "fallback_provider",
        field="llm.embedding.fallback_provider",
        collector=collector,
    )
    if fallback_name and fallback_name != embedding_name:
        if fallback_name not in EMBEDDING_PROVIDERS:
            collector.add(
                "unknown_provider",
                "llm.embedding.fallback_provider",
                provider=fallback_name,
                credential_configured=unknown_credential_configured(raw.get(fallback_name)),
                reason="legacy_embedding_fallback_has_no_safe_mapping",
            )
        else:
            fallback_raw = (
                provider_tables.get(fallback_name, {})
                if fallback_name in provider_tables
                else raw_table(
                    raw,
                    fallback_name,
                    field=f"llm.{fallback_name}",
                    collector=collector,
                )
            )
            fallback_mapped = map_embedding_provider(
                fallback_name,
                fallback_raw,
                fallback_raw if fallback_enabled else {},
                environment,
                used_ids,
                collector,
                endpoint_prefix=f"llm.{fallback_name}",
            )
            fallback_provider = fallback_mapped.provider
            fallback_space = embedding_space(fallback_name)
            fallback_state = _EmbeddingProviderState(
                provider_id=fallback_provider.id,
                space=fallback_space,
                endpoint_valid=fallback_mapped.endpoint_valid,
            )
            if not fallback_enabled:
                if fallback_provider.credential.source != "none":
                    collector.add(
                        "unused_credential",
                        "llm.embedding.fallback_provider",
                        provider=fallback_name,
                        credential_configured=True,
                        reason="embedding_fallback_is_disabled",
                    )
            elif providers and embedding_space_compatible(
                fallback_provider,
                fallback_space,
                settings,
                endpoint_valid=fallback_mapped.endpoint_valid,
            ):
                providers.append(fallback_provider)
                embedding_states.append(fallback_state)
            else:
                issue = collector.add(
                    "embedding_space_mismatch",
                    "llm.embedding.fallback_provider",
                    provider=fallback_name,
                    credential_configured=fallback_provider.credential.source != "none",
                    reason="effective_embedding_space_differs_or_provider_is_unusable",
                    allowed_actions=EMBEDDING_MISMATCH_ACTIONS,
                )
                pending.append(
                    _PendingValue(
                        issue_id=issue.id,
                        embedding_provider=fallback_provider,
                        embedding_state=fallback_state,
                    )
                )
    return EmbeddingRouteConfig(
        enabled=bool(embedding_name and providers),
        settings=settings,
        providers=tuple(providers),
    )


def _report_unknown_top_level(raw: Mapping[str, object], collector: IssueCollector) -> None:
    for name in sorted(set(raw) - KNOWN_LLM_FIELDS):
        value = raw[name]
        if isinstance(value, Mapping):
            collector.add(
                "unknown_provider",
                f"llm.{name}",
                provider=name,
                credential_configured=unknown_credential_configured(value),
                reason="legacy_provider_table_has_no_safe_mapping",
            )
        else:
            collector.add(
                "unknown_legacy_field",
                f"llm.{name}",
                credential_configured=unknown_credential_configured(value),
                reason="legacy_field_has_no_safe_mapping",
            )


def migrate_legacy_llm(
    raw_llm: Mapping[str, object],
    env: Mapping[str, str],
) -> LegacyMigrationResult:
    """Build a deterministic, secret-safe, read-only candidate from legacy data."""
    raw = {key: value for key, value in raw_llm.items() if isinstance(key, str)}
    environment = {str(key): str(value) for key, value in env.items()}
    collector = IssueCollector()
    used_ids: set[str] = set()
    pending: list[_PendingValue] = []
    embedding_states: list[_EmbeddingProviderState] = []
    provider_tables, embedding_raw, module_tables = _known_tables(raw, collector)

    chat_connections = _map_chat_routes(
        raw,
        provider_tables,
        environment,
        used_ids,
        collector,
        pending,
    )
    _report_module_overrides(module_tables, collector)
    embedding = _map_embedding_route(
        raw,
        embedding_raw,
        provider_tables,
        environment,
        used_ids,
        collector,
        pending,
        embedding_states,
    )
    _report_unknown_top_level(raw, collector)

    models = ModelConfig(
        schema_version=1,
        chat=ChatRouteConfig(
            connections=chat_connections,
            concurrency=exact_int_field(
                raw,
                "concurrency",
                field="llm.concurrency",
                collector=collector,
                default=4,
                minimum=1,
                maximum=16,
                reason="legacy_integer_value_is_invalid",
            ),
            timeout_seconds=exact_int_field(
                raw,
                "timeout",
                field="llm.timeout",
                collector=collector,
                default=300,
                minimum=10,
                maximum=None,
                reason="legacy_integer_value_is_invalid",
            ),
        ),
        embedding=embedding,
    )
    return LegacyMigrationResult(
        models=models,
        report=MigrationReport(tuple(collector.issues)),
        _pending=tuple(pending),
        _embedding_states=tuple(embedding_states),
    )


__all__ = ["migrate_legacy_llm"]
