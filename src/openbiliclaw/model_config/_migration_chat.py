"""Focused mapping of legacy Chat providers into typed connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._migration_constants import (
    CHAT_DEFAULT_MODELS,
    OFFICIAL_PATHS,
    PROVIDER_LABELS,
)
from ._migration_inspection import (
    IssueCollector,
    exact_int_field,
    inspect_credential_from_raw,
    inspect_endpoint,
    legacy_connection_id,
    normalized_ollama_endpoint,
    text_field,
)
from .types import ChatConnection, CredentialConfig

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class MappedChat:
    """One mapped Chat connection and issues that remove it when confirmed."""

    connection: ChatConnection
    removal_issue_ids: tuple[str, ...] = ()


def _missing_credential_issue(
    provider: str,
    field: str,
    collector: IssueCollector,
) -> str:
    issue = collector.add(
        "invalid_legacy_value",
        field,
        provider=provider,
        credential_configured=False,
        reason="configured_provider_has_no_credential",
    )
    return issue.id


def map_chat_connection(
    provider: str,
    raw: Mapping[str, object],
    env: Mapping[str, str],
    used_ids: set[str],
    collector: IssueCollector,
) -> MappedChat:
    """Map one known legacy Chat provider without retaining rejected values."""
    prefix = f"llm.{provider}"
    model = (
        text_field(
            raw,
            "model",
            field=f"{prefix}.model",
            collector=collector,
            default=CHAT_DEFAULT_MODELS[provider],
        ).value
        or CHAT_DEFAULT_MODELS[provider]
    )
    inspected_credential = inspect_credential_from_raw(
        provider,
        raw,
        env,
        prefix=prefix,
        collector=collector,
    )
    credential = inspected_credential.credential
    connection_id = legacy_connection_id("chat", provider, used_ids)
    name = PROVIDER_LABELS[provider]
    removal_issue_ids: list[str] = []

    if provider == "openai":
        auth_mode = text_field(
            raw,
            "auth_mode",
            field="llm.openai.auth_mode",
            collector=collector,
        ).value.lower()
        endpoint = inspect_endpoint(
            raw.get("base_url", ""),
            field="llm.openai.base_url",
            collector=collector,
            official_host="api.openai.com",
            official_paths=OFFICIAL_PATHS,
            canonical_official="https://api.openai.com/v1",
        )
        if auth_mode == "codex_oauth":
            if credential.source == "inline":
                collector.add(
                    "unused_credential",
                    "llm.openai.api_key",
                    provider=provider,
                    credential_configured=True,
                    reason="codex_oauth_does_not_use_inline_api_key",
                )
            if not endpoint.official:
                collector.add(
                    "invalid_auth_mode",
                    "llm.openai.base_url",
                    provider=provider,
                    credential_configured=True,
                    reason="codex_oauth_requires_official_endpoint",
                )
            return MappedChat(
                ChatConnection(
                    id=connection_id,
                    name="Codex OAuth",
                    type="codex_oauth",
                    model=model,
                    credential=CredentialConfig(source="oauth", value="codex"),
                )
            )
        if auth_mode not in {"", "api_key"}:
            collector.add(
                "invalid_auth_mode",
                "llm.openai.auth_mode",
                provider=provider,
                credential_configured=credential.source != "none",
                reason="legacy_auth_mode_is_not_supported",
            )
        if credential.source == "none":
            removal_issue_ids.append(
                inspected_credential.issue_id
                or _missing_credential_issue(provider, "llm.openai.api_key", collector)
            )
        if not endpoint.valid and endpoint.issue_id:
            removal_issue_ids.append(endpoint.issue_id)

        api_flavor = text_field(
            raw,
            "api_flavor",
            field="llm.openai.api_flavor",
            collector=collector,
        ).value.lower()
        if api_flavor:
            collector.add(
                "translated_legacy_field",
                "llm.openai.api_flavor",
                provider=provider,
                reason="renamed_to_api_mode",
                severity="warning",
                allowed_actions=(),
            )
        if api_flavor not in {"", "chat_completions", "responses"}:
            collector.add(
                "invalid_legacy_value",
                "llm.openai.api_flavor",
                provider=provider,
                reason="legacy_api_mode_is_not_supported",
            )
            api_flavor = ""
        return MappedChat(
            ChatConnection(
                id=connection_id,
                name=name,
                type="openai_compatible",
                preset="openai" if endpoint.official else "custom",
                model=model,
                base_url=endpoint.value,
                credential=credential,
                api_mode=api_flavor or "chat_completions",
            ),
            tuple(removal_issue_ids),
        )

    if provider != "ollama" and credential.source == "none":
        removal_issue_ids.append(
            inspected_credential.issue_id
            or _missing_credential_issue(provider, f"llm.{provider}.api_key", collector)
        )

    if provider == "claude":
        endpoint = inspect_endpoint(
            raw.get("base_url", ""),
            field="llm.claude.base_url",
            collector=collector,
            official_host="api.anthropic.com",
            official_paths=OFFICIAL_PATHS,
            canonical_official="https://api.anthropic.com",
        )
        if not endpoint.valid and endpoint.issue_id:
            removal_issue_ids.append(endpoint.issue_id)
        return MappedChat(
            ChatConnection(
                id=connection_id,
                name=name,
                type="anthropic_compatible",
                preset="anthropic" if endpoint.official else "custom",
                model=model,
                base_url=endpoint.value,
                credential=credential,
            ),
            tuple(removal_issue_ids),
        )

    if provider == "gemini":
        endpoint = inspect_endpoint(
            raw.get("base_url", ""),
            field="llm.gemini.base_url",
            collector=collector,
        )
        return MappedChat(
            ChatConnection(
                id=connection_id,
                name=name,
                type="gemini_api",
                model=model,
                base_url=endpoint.value,
                credential=credential,
            ),
            tuple(removal_issue_ids),
        )

    if provider == "deepseek":
        endpoint = inspect_endpoint(
            raw.get("base_url", ""),
            field="llm.deepseek.base_url",
            collector=collector,
            official_host="api.deepseek.com",
            official_paths=OFFICIAL_PATHS,
            canonical_official="https://api.deepseek.com",
        )
        if endpoint.valid and not endpoint.official:
            collector.add(
                "invalid_legacy_value",
                "llm.deepseek.base_url",
                provider=provider,
                credential_configured=credential.source != "none",
                reason="legacy_runtime_ignored_custom_deepseek_endpoint",
            )
        reasoning_effort = text_field(
            raw,
            "reasoning_effort",
            field="llm.deepseek.reasoning_effort",
            collector=collector,
        ).value.lower()
        return MappedChat(
            ChatConnection(
                id=connection_id,
                name=name,
                type="openai_compatible",
                preset="deepseek",
                model=model,
                base_url="https://api.deepseek.com",
                credential=credential,
                api_mode="chat_completions",
                reasoning_effort=reasoning_effort,
            ),
            tuple(removal_issue_ids),
        )

    if provider == "ollama":
        if credential.source == "inline":
            collector.add(
                "unused_credential",
                "llm.ollama.api_key",
                provider=provider,
                credential_configured=True,
                reason="ollama_does_not_use_legacy_api_key",
            )
        endpoint = inspect_endpoint(
            raw.get("base_url", ""),
            field="llm.ollama.base_url",
            collector=collector,
            default="http://127.0.0.1:11434/v1",
        )
        if not endpoint.valid and endpoint.issue_id:
            removal_issue_ids.append(endpoint.issue_id)
        num_ctx = exact_int_field(
            raw,
            "num_ctx",
            field="llm.ollama.num_ctx",
            collector=collector,
            default=0,
            minimum=0,
            maximum=None,
            reason="legacy_integer_value_is_invalid",
        )
        return MappedChat(
            ChatConnection(
                id=connection_id,
                name=name,
                type="ollama",
                model=model,
                base_url=normalized_ollama_endpoint(endpoint.value),
                num_ctx=num_ctx,
            ),
            tuple(removal_issue_ids),
        )

    if provider == "openrouter":
        endpoint = inspect_endpoint(
            raw.get("base_url", ""),
            field="llm.openrouter.base_url",
            collector=collector,
            default="https://openrouter.ai/api/v1",
        )
        if not endpoint.valid and endpoint.issue_id:
            removal_issue_ids.append(endpoint.issue_id)
        referer = inspect_endpoint(
            raw.get("http_referer", ""),
            field="llm.openrouter.http_referer",
            collector=collector,
        )
        x_title = text_field(
            raw,
            "x_title",
            field="llm.openrouter.x_title",
            collector=collector,
        ).value
        return MappedChat(
            ChatConnection(
                id=connection_id,
                name=name,
                type="openai_compatible",
                preset="openrouter",
                model=model,
                base_url=endpoint.value,
                credential=credential,
                api_mode="chat_completions",
                http_referer=referer.value,
                x_title=x_title,
            ),
            tuple(removal_issue_ids),
        )

    endpoint = inspect_endpoint(
        raw.get("base_url", ""),
        field="llm.openai_compatible.base_url",
        collector=collector,
        required=True,
    )
    if not endpoint.valid and endpoint.issue_id:
        removal_issue_ids.append(endpoint.issue_id)
    api_flavor = text_field(
        raw,
        "api_flavor",
        field="llm.openai_compatible.api_flavor",
        collector=collector,
    ).value.lower()
    if api_flavor:
        collector.add(
            "translated_legacy_field",
            "llm.openai_compatible.api_flavor",
            provider=provider,
            reason="renamed_to_api_mode",
            severity="warning",
            allowed_actions=(),
        )
    if api_flavor not in {"", "chat_completions", "responses"}:
        collector.add(
            "invalid_legacy_value",
            "llm.openai_compatible.api_flavor",
            provider=provider,
            reason="legacy_api_mode_is_not_supported",
        )
        api_flavor = ""
    return MappedChat(
        ChatConnection(
            id=connection_id,
            name=name,
            type="openai_compatible",
            preset="custom",
            model=model,
            base_url=endpoint.value,
            credential=credential,
            api_mode=api_flavor or "chat_completions",
        ),
        tuple(removal_issue_ids),
    )


__all__ = ["MappedChat", "map_chat_connection"]
