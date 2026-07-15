"""Tests for deterministic, read-only migration of legacy ``[llm]`` data."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.model_config import (
    ChatConnection,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    LegacyMigrationResult,
    MigrationAction,
    MigrationReport,
    MigrationResolution,
    MigrationResolutionError,
    ModelConfig,
    apply_migration_resolutions,
    compute_model_revision,
    connection_type_registry,
    migrate_legacy_llm,
    validate_model_config,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _migrate(raw: dict[str, object], env: dict[str, str] | None = None) -> LegacyMigrationResult:
    return migrate_legacy_llm(raw, env or {})


def _resolution(
    action: MigrationAction | str,
    *,
    position: int | None = None,
    embedding_settings: EmbeddingModelSettings | None = None,
) -> MigrationResolution:
    return MigrationResolution(
        action=cast("MigrationAction", action),
        position=position,
        embedding_settings=embedding_settings,
    )


def _apply(
    result: LegacyMigrationResult,
    choices: dict[str, MigrationResolution],
) -> ModelConfig:
    return apply_migration_resolutions(result, choices)


_CHAT_MODELS = {
    "openai": "gpt-5-nano",
    "deepseek": "deepseek-v4-flash",
    "openrouter": "openai/gpt-5-nano",
    "openai_compatible": "custom-chat",
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "ollama": "qwen2.5:7b",
}


def legacy_provider(provider: str, *, base_url: str = "") -> dict[str, object]:
    provider_config: dict[str, object] = {
        "model": _CHAT_MODELS[provider],
        "base_url": base_url,
    }
    if provider != "ollama":
        provider_config["api_key"] = f"secret-{provider}"
    return {
        "default_provider": provider,
        provider: provider_config,
    }


@pytest.mark.parametrize(
    ("legacy", "expected_type", "expected_preset"),
    [
        (legacy_provider("openai"), "openai_compatible", "openai"),
        (
            legacy_provider("openai", base_url="https://relay.example/v1"),
            "openai_compatible",
            "custom",
        ),
        (legacy_provider("deepseek"), "openai_compatible", "deepseek"),
        (legacy_provider("openrouter"), "openai_compatible", "openrouter"),
        (legacy_provider("openai_compatible"), "openai_compatible", "custom"),
        (legacy_provider("claude"), "anthropic_compatible", "anthropic"),
        (
            legacy_provider("claude", base_url="https://relay.example"),
            "anthropic_compatible",
            "custom",
        ),
        (legacy_provider("gemini"), "gemini_api", ""),
        (legacy_provider("ollama"), "ollama", ""),
    ],
)
def test_legacy_provider_mapping(
    legacy: dict[str, object], expected_type: str, expected_preset: str
) -> None:
    result = _migrate(legacy)
    connection = result.models.chat.connections[0]

    assert (connection.type, connection.preset) == (expected_type, expected_preset)


def test_official_provider_hosts_keep_official_presets() -> None:
    openai = legacy_provider("openai", base_url="https://API.OpenAI.com/v1/")
    anthropic = legacy_provider("claude", base_url="https://api.anthropic.com/v1")

    openai_connection = _migrate(openai).models.chat.connections[0]
    anthropic_connection = _migrate(anthropic).models.chat.connections[0]

    assert (openai_connection.preset, openai_connection.base_url) == (
        "openai",
        "https://api.openai.com/v1",
    )
    assert (anthropic_connection.preset, anthropic_connection.base_url) == (
        "anthropic",
        "https://api.anthropic.com",
    )


@pytest.mark.parametrize(
    ("provider", "raw_url"),
    [
        ("openai", "https://user:password@api.openai.com/v1"),
        ("openai", "https://api.openai.com/v1?fake_token=secret-query"),
        ("openai", "https://api.openai.com/v1#secret-fragment"),
        ("openai", "https://api.openai.com:444/v1"),
        ("openai", "https://api.openai.com/v1/unexpected"),
        ("openai", " https://api.openai.com/v1"),
        ("openai", "https://api.openai.com/v1?"),
        ("openai", "https://api.openai.com/v1#"),
        ("openai", "https://api.openai.com/v1//"),
        ("openai", r"https://api.openai.com\unexpected/v1"),
        ("claude", "https://user:password@api.anthropic.com/v1"),
        ("claude", "https://api.anthropic.com/v1?fake_token=secret-query"),
        ("claude", "https://api.anthropic.com/v1#secret-fragment"),
        ("claude", "https://api.anthropic.com:444/v1"),
        ("claude", "https://api.anthropic.com/v1/unexpected"),
    ],
)
def test_credential_bearing_or_noncanonical_official_urls_are_rejected_safely(
    provider: str,
    raw_url: str,
) -> None:
    result = _migrate(legacy_provider(provider, base_url=raw_url))
    connection = result.models.chat.connections[0]
    issue = next(item for item in result.report.issues if item.field.endswith(".base_url"))

    assert connection.preset == "custom"
    assert connection.base_url == ""
    assert issue.code == "invalid_legacy_value"
    assert issue.reason == "legacy_endpoint_is_invalid"
    assert raw_url not in repr(result)
    assert raw_url not in repr(result.report)

    with pytest.raises(MigrationResolutionError) as raised:
        _apply(result, {issue.id: _resolution("cancel")})
    assert raw_url not in str(raised.value)


@pytest.mark.parametrize("provider", ["openai", "claude"])
def test_valid_custom_provider_urls_are_normalized_but_hidden_from_repr(provider: str) -> None:
    raw_url = "https://Relay.Example:8443/v1/endpoint/"
    result = _migrate(legacy_provider(provider, base_url=raw_url))
    connection = result.models.chat.connections[0]

    assert connection.preset == "custom"
    assert connection.base_url == "https://relay.example:8443/v1/endpoint/"
    assert raw_url not in repr(result)
    assert connection.base_url not in repr(connection)


def test_explicit_null_endpoint_is_reported_instead_of_treated_as_absent() -> None:
    legacy = legacy_provider("openai")
    cast("dict[str, object]", legacy["openai"])["base_url"] = None

    result = _migrate(legacy)
    connection = result.models.chat.connections[0]
    issue = next(item for item in result.report.issues if item.field == "llm.openai.base_url")

    assert connection.preset == "custom"
    assert connection.base_url == ""
    assert issue.code == "invalid_legacy_value"
    assert issue.reason == "legacy_endpoint_must_be_string"


def test_connection_reprs_hide_credential_adjacent_url_fields() -> None:
    secret_url = "https://user:secret-password@example.invalid/v1?token=secret"
    chat = ChatConnection(
        id="chat",
        name="Chat",
        type="openai_compatible",
        model="model",
        base_url=secret_url,
        http_referer=secret_url,
    )
    embedding = EmbeddingProviderConfig(
        id="embedding",
        name="Embedding",
        type="openai_compatible",
        base_url=secret_url,
    )

    assert secret_url not in repr(chat)
    assert secret_url not in repr(embedding)


def test_codex_oauth_becomes_a_reference_without_copying_inline_token() -> None:
    legacy = legacy_provider("openai")
    legacy["openai"].update(
        auth_mode="codex_oauth",
        api_key="inline-key-that-codex-ignored",
    )

    result = _migrate(legacy)
    connection = result.models.chat.connections[0]

    assert connection.type == "codex_oauth"
    assert connection.preset == ""
    assert connection.credential == CredentialConfig(source="oauth", value="codex")
    assert "inline-key-that-codex-ignored" not in repr(result)
    unused = next(issue for issue in result.report.issues if issue.code == "unused_credential")
    assert unused.field == "llm.openai.api_key"
    assert unused.credential_configured is True
    assert unused.allowed_actions == ("confirm_remove_after_backup", "cancel")


def test_provider_specific_fields_map_to_the_new_connection() -> None:
    legacy = {
        "default_provider": "deepseek",
        "concurrency": 6,
        "timeout": 95,
        "deepseek": {
            "api_key": "deepseek-secret",
            "model": "deepseek-v4-pro",
            "base_url": "https://api.deepseek.com",
            "reasoning_effort": "high",
        },
        "openrouter": {
            "api_key": "router-secret",
            "model": "google/gemini-2.5-flash",
            "base_url": "https://openrouter.ai/api/v1",
            "http_referer": "https://openbiliclaw.local",
            "x_title": "OpenBiliClaw",
        },
        "fallback_provider": "openrouter",
    }

    result = _migrate(legacy)
    deepseek, router = result.models.chat.connections

    assert result.models.chat.concurrency == 6
    assert result.models.chat.timeout_seconds == 95
    assert deepseek.model == "deepseek-v4-pro"
    assert deepseek.reasoning_effort == "high"
    assert deepseek.credential == CredentialConfig(source="inline", value="deepseek-secret")
    assert router.http_referer == "https://openbiliclaw.local"
    assert router.x_title == "OpenBiliClaw"
    assert router.credential == CredentialConfig(source="inline", value="router-secret")


def test_openai_api_flavor_is_safely_translated_to_api_mode() -> None:
    legacy = legacy_provider("openai")
    legacy["openai"]["api_flavor"] = "responses"

    result = _migrate(legacy)

    assert result.models.chat.connections[0].api_mode == "responses"
    issue = next(issue for issue in result.report.issues if issue.code == "translated_legacy_field")
    assert issue.field == "llm.openai.api_flavor"
    assert issue.allowed_actions == ()


def test_ollama_url_and_context_are_mapped_to_effective_values() -> None:
    legacy = legacy_provider("ollama", base_url="http://127.0.0.1:11434")
    legacy["ollama"]["num_ctx"] = 8192

    connection = _migrate(legacy).models.chat.connections[0]

    assert connection.base_url == "http://127.0.0.1:11434/v1"
    assert connection.num_ctx == 8192


def test_gemini_environment_credential_keeps_only_the_variable_name() -> None:
    legacy = legacy_provider("gemini")
    legacy["gemini"]["api_key"] = ""

    result = _migrate(legacy, {"GOOGLE_API_KEY": "environment-secret"})
    connection = result.models.chat.connections[0]

    assert connection.credential == CredentialConfig(source="env", value="GOOGLE_API_KEY")
    assert "environment-secret" not in repr(result)
    assert "environment-secret" not in repr(result.report)


def test_routed_remote_provider_without_credential_is_a_blocking_decision() -> None:
    legacy = legacy_provider("openai")
    legacy["openai"]["api_key"] = ""

    result = _migrate(legacy)

    issue = next(issue for issue in result.report.issues if issue.field == "llm.openai.api_key")
    assert issue.code == "invalid_legacy_value"
    assert issue.credential_configured is False
    assert issue.allowed_actions == ("confirm_remove_after_backup", "cancel")


def legacy_with_three_configured_providers() -> dict[str, object]:
    return {
        "default_provider": "deepseek",
        "fallback_provider": "openrouter",
        "deepseek": {
            "api_key": "deepseek-secret",
            "model": "deepseek-v4-flash",
        },
        "openrouter": {
            "api_key": "router-secret",
            "model": "openai/gpt-5-nano",
        },
        "openai": {"api_key": "openai-secret", "model": "gpt-5-nano"},
    }


def test_only_explicit_default_and_fallback_enter_chat_route() -> None:
    result = _migrate(legacy_with_three_configured_providers())

    assert [item.preset for item in result.models.chat.connections] == [
        "deepseek",
        "openrouter",
    ]
    assert result.report.issue_codes == {"unrouted_credential"}
    issue = result.report.issues[0]
    assert issue.provider == "openai"
    assert issue.credential_configured is True
    assert issue.allowed_actions == (
        "add_to_chat_route",
        "confirm_remove_after_backup",
        "cancel",
    )


def legacy_embedding(
    *,
    primary_model: str = "bge-m3",
    fallback_provider: str = "openai",
) -> dict[str, object]:
    return {
        "default_provider": "openai",
        "openai": {"api_key": "openai-secret", "model": "gpt-5-nano"},
        "ollama": {
            "model": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434/v1",
        },
        "embedding": {
            "provider": "ollama",
            "model": primary_model,
            "base_url": "http://127.0.0.1:11434/v1",
            "output_dimensionality": 1024,
            "similarity_threshold": 0.82,
            "fallback_enabled": True,
            "fallback_provider": fallback_provider,
            "multimodal_enabled": False,
        },
    }


def test_incompatible_embedding_fallback_is_reported_not_mapped() -> None:
    result = _migrate(legacy_embedding(primary_model="bge-m3"))

    assert len(result.models.embedding.providers) == 1
    assert "embedding_space_mismatch" in result.report.issue_codes
    issue = next(item for item in result.report.issues if item.code == "embedding_space_mismatch")
    assert issue.allowed_actions == (
        "apply_shared_embedding_settings",
        "remove_embedding_fallback",
        "cancel",
    )


def test_compatible_embedding_fallback_uses_one_shared_space() -> None:
    legacy = {
        "default_provider": "deepseek",
        "fallback_provider": "openai",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai": {"api_key": "openai-secret", "model": "gpt-5-nano"},
        "openai_compatible": {
            "api_key": "embedding-gateway-secret",
            "model": "unused-chat-model",
            "base_url": "https://embedding.example/v1",
        },
        "embedding": {
            "provider": "openai_compatible",
            "model": "text-embedding-3-small",
            "api_key": "dedicated-embedding-secret",
            "base_url": "https://embedding.example/v1",
            "output_dimensionality": 1536,
            "similarity_threshold": 0.76,
            "fallback_enabled": True,
            "fallback_provider": "openai",
            "multimodal_enabled": False,
        },
    }

    result = _migrate(legacy)

    assert [provider.preset for provider in result.models.embedding.providers] == [
        "custom",
        "openai",
    ]
    assert result.models.embedding.settings == EmbeddingModelSettings(
        model="text-embedding-3-small",
        output_dimensionality=1536,
        similarity_threshold=0.76,
        multimodal_enabled=False,
    )
    assert "embedding_space_mismatch" not in result.report.issue_codes


def test_remote_embedding_primary_without_credential_is_reported() -> None:
    legacy = legacy_provider("openai")
    legacy["embedding"] = {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "api_key": "",
        "base_url": "",
        "fallback_enabled": False,
    }

    result = _migrate(legacy)

    issue = next(issue for issue in result.report.issues if issue.field == "llm.embedding.api_key")
    assert issue.code == "invalid_legacy_value"
    assert issue.credential_configured is False


@pytest.mark.parametrize("provider", ["", "ollama"])
def test_unused_embedding_credential_remains_visible_without_its_value(
    provider: str,
) -> None:
    legacy = legacy_provider("deepseek")
    legacy["embedding"] = {
        "provider": provider,
        "model": "bge-m3",
        "api_key": "unused-embedding-secret",
    }

    result = _migrate(legacy)

    issue = next(issue for issue in result.report.issues if issue.code == "unused_credential")
    assert issue.field == "llm.embedding.api_key"
    assert issue.credential_configured is True
    assert "unused-embedding-secret" not in repr(result.report)


def test_embedding_fallback_without_effective_credential_is_not_mapped() -> None:
    legacy = legacy_embedding(primary_model="text-embedding-3-small")
    legacy["openai"]["api_key"] = ""

    result = _migrate(legacy)

    assert len(result.models.embedding.providers) == 1
    assert "embedding_space_mismatch" in result.report.issue_codes


@pytest.mark.parametrize(
    ("fallback_provider", "shared_model", "env"),
    [
        ("ollama", "bge-m3", {}),
        ("gemini", "gemini-embedding-001", {"GOOGLE_API_KEY": "ambient-secret"}),
    ],
)
def test_disabled_embedding_fallback_is_never_admitted(
    fallback_provider: str,
    shared_model: str,
    env: dict[str, str],
) -> None:
    legacy: dict[str, object] = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai_compatible": {
            "api_key": "primary-secret",
            "model": "unused-chat-model",
            "base_url": "https://embedding.example/v1",
        },
        "embedding": {
            "provider": "openai_compatible",
            "model": shared_model,
            "api_key": "primary-embedding-secret",
            "base_url": "https://embedding.example/v1",
            "output_dimensionality": 1024,
            "fallback_enabled": False,
            "fallback_provider": fallback_provider,
            "multimodal_enabled": False,
        },
    }

    result = _migrate(legacy, env)

    assert len(result.models.embedding.providers) == 1
    assert result.models.embedding.providers[0].preset == "custom"
    if fallback_provider == "gemini":
        unused = next(
            item
            for item in result.report.issues
            if item.provider == "gemini" and item.credential_configured
        )
        assert unused.code in {"unused_credential", "unrouted_credential"}
        assert "ambient-secret" not in repr(result)


def test_invalid_embedding_fallback_enabled_type_is_reported_and_never_enabled() -> None:
    legacy: dict[str, object] = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai_compatible": {
            "api_key": "primary-secret",
            "model": "unused-chat-model",
            "base_url": "https://embedding.example/v1",
        },
        "ollama": {"model": "bge-m3", "base_url": "http://127.0.0.1:11434/v1"},
        "embedding": {
            "provider": "openai_compatible",
            "model": "bge-m3",
            "api_key": "primary-embedding-secret",
            "base_url": "https://embedding.example/v1",
            "output_dimensionality": 1024,
            "fallback_enabled": "true",
            "fallback_provider": "ollama",
            "multimodal_enabled": False,
        },
    }

    result = _migrate(legacy)
    issue = next(
        item for item in result.report.issues if item.field == "llm.embedding.fallback_enabled"
    )

    assert issue.code == "invalid_legacy_value"
    assert issue.reason == "embedding_fallback_enabled_must_be_boolean"
    assert len(result.models.embedding.providers) == 1


def test_embedding_fallback_requires_exact_effective_output_dimension() -> None:
    legacy: dict[str, object] = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai_compatible": {
            "api_key": "primary-secret",
            "model": "unused-chat-model",
            "base_url": "https://embedding.example/v1",
        },
        "ollama": {"model": "bge-m3", "base_url": "http://127.0.0.1:11434/v1"},
        "embedding": {
            "provider": "openai_compatible",
            "model": "bge-m3",
            "api_key": "primary-embedding-secret",
            "base_url": "https://embedding.example/v1",
            "output_dimensionality": 768,
            "fallback_enabled": True,
            "fallback_provider": "ollama",
            "multimodal_enabled": False,
        },
    }

    result = _migrate(legacy)

    assert len(result.models.embedding.providers) == 1
    assert "embedding_space_mismatch" in result.report.issue_codes


def test_embedding_fallback_requires_model_specific_multimodal_capability() -> None:
    legacy: dict[str, object] = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai_compatible": {
            "api_key": "primary-secret",
            "model": "unused-chat-model",
            "base_url": "https://embedding.example/v1",
        },
        "gemini": {"api_key": "gemini-secret", "model": "gemini-2.5-flash"},
        "embedding": {
            "provider": "openai_compatible",
            "model": "gemini-embedding-001",
            "api_key": "primary-embedding-secret",
            "base_url": "https://embedding.example/v1",
            "output_dimensionality": 1024,
            "fallback_enabled": True,
            "fallback_provider": "gemini",
            "multimodal_enabled": True,
        },
    }

    result = _migrate(legacy)

    assert len(result.models.embedding.providers) == 1
    assert "embedding_space_mismatch" in result.report.issue_codes


def test_embedding_fallback_requires_a_usable_provider_configuration() -> None:
    legacy: dict[str, object] = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai": {"api_key": "primary-secret", "model": "gpt-5-nano"},
        "openai_compatible": {
            "api_key": "fallback-secret",
            "model": "unused-chat-model",
            "base_url": "",
        },
        "embedding": {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "api_key": "primary-embedding-secret",
            "output_dimensionality": 1536,
            "fallback_enabled": True,
            "fallback_provider": "openai_compatible",
            "multimodal_enabled": False,
        },
    }

    result = _migrate(legacy)

    assert len(result.models.embedding.providers) == 1
    assert "embedding_space_mismatch" in result.report.issue_codes


@pytest.mark.parametrize(
    ("fallback_provider", "model", "multimodal", "env"),
    [
        (
            "gemini",
            "gemini-embedding-001",
            False,
            {"GOOGLE_API_KEY": "gemini-env-secret"},
        ),
        (
            "dashscope",
            "qwen3-vl-embedding",
            True,
            {"DASHSCOPE_API_KEY": "dashscope-env-secret"},
        ),
    ],
)
def test_compatible_official_embedding_fallback_does_not_require_a_base_url(
    fallback_provider: str,
    model: str,
    multimodal: bool,
    env: dict[str, str],
) -> None:
    legacy: dict[str, object] = {
        "default_provider": "deepseek",
        "deepseek": {"api_key": "deepseek-secret", "model": "deepseek-v4-flash"},
        "openai_compatible": {
            "api_key": "primary-secret",
            "model": "unused-chat-model",
            "base_url": "https://embedding.example/v1",
        },
        "embedding": {
            "provider": "openai_compatible",
            "model": model,
            "api_key": "primary-embedding-secret",
            "base_url": "https://embedding.example/v1",
            "output_dimensionality": 1024,
            "fallback_enabled": True,
            "fallback_provider": fallback_provider,
            "multimodal_enabled": multimodal,
        },
    }

    result = _migrate(legacy, env)

    assert len(result.models.embedding.providers) == 2
    assert result.models.embedding.providers[1].type in {"gemini_api", "dashscope_api"}
    assert all(secret not in repr(result) for secret in env.values())


def legacy_with_module_override(module: str, provider: str) -> dict[str, object]:
    legacy = legacy_provider("deepseek")
    legacy[module] = {"provider": provider, "model": "module-only-model"}
    return legacy


def test_module_overrides_require_explicit_global_route_acknowledgement() -> None:
    result = _migrate(legacy_with_module_override("evaluation", "openai"))
    issue = next(item for item in result.report.issues if item.code == "module_override_removed")

    assert issue.field == "llm.evaluation"
    assert issue.allowed_actions == ("accept_global_route", "cancel")


def test_unknown_provider_and_invalid_auth_mode_remain_visible_and_safe() -> None:
    unknown = {
        "default_provider": "translated-provider",
        "translated-provider": {"api_key": "unknown-provider-secret", "model": "m"},
    }
    invalid_auth = legacy_provider("openai")
    invalid_auth["openai"]["auth_mode"] = "stale-auth-mode"

    unknown_result = _migrate(unknown)
    auth_result = _migrate(invalid_auth)

    unknown_issue = next(
        issue for issue in unknown_result.report.issues if issue.code == "unknown_provider"
    )
    auth_issue = next(
        issue for issue in auth_result.report.issues if issue.code == "invalid_auth_mode"
    )
    assert unknown_issue.field == "llm.default_provider"
    assert unknown_issue.provider == "translated-provider"
    assert unknown_issue.credential_configured is True
    assert auth_issue.field == "llm.openai.auth_mode"
    assert unknown_issue.allowed_actions == ("confirm_remove_after_backup", "cancel")
    assert auth_issue.allowed_actions == ("confirm_remove_after_backup", "cancel")
    assert "unknown-provider-secret" not in repr(unknown_result)
    assert "secret-openai" not in repr(auth_result.report)


def test_unknown_provider_fields_and_stale_embedding_fields_are_reported() -> None:
    legacy = legacy_provider("deepseek")
    legacy["deepseek"]["vendor_option"] = "opaque-value"
    legacy["embedding"] = {
        "provider": "",
        "fallback_enabled": False,
        "retired_option": "opaque-embedding-value",
    }

    result = _migrate(legacy)

    fields = {issue.field for issue in result.report.issues}
    assert "llm.deepseek.vendor_option" in fields
    assert "llm.embedding.retired_option" in fields
    assert "opaque-value" not in repr(result.report)
    assert "opaque-embedding-value" not in repr(result.report)


@pytest.mark.parametrize("section", ["openai", "embedding", "evaluation"])
def test_known_legacy_sections_must_be_tables_without_echoing_the_raw_value(
    section: str,
) -> None:
    raw = legacy_provider("deepseek")
    if section == "openai":
        raw["default_provider"] = "openai"
    raw[section] = "secret-section-payload"

    result = _migrate(raw)
    issue = next(item for item in result.report.issues if item.field == f"llm.{section}")

    assert issue.code == "invalid_legacy_value"
    assert issue.reason == "legacy_section_must_be_table"
    assert issue.credential_configured is True
    assert "secret-section-payload" not in repr(result)
    assert "secret-section-payload" not in repr(result.report)


def test_non_string_provider_selector_is_reported_without_coercion() -> None:
    result = _migrate(
        {
            "default_provider": ["openai", "secret-selector"],
            "openai": {"api_key": "openai-secret", "model": "gpt-5-nano"},
        }
    )
    issue = next(item for item in result.report.issues if item.field == "llm.default_provider")

    assert issue.code == "invalid_legacy_value"
    assert issue.reason == "legacy_provider_name_must_be_string"
    assert result.models.chat.connections == ()
    assert "secret-selector" not in repr(result)


def test_non_string_provider_fields_are_reported_and_never_retained() -> None:
    raw: dict[str, object] = {
        "default_provider": "openai",
        "openai": {
            "api_key": {"token": "credential-object-secret"},
            "model": ["model-secret"],
            "base_url": 12345,
            "auth_mode": True,
        },
    }

    result = _migrate(raw)
    issues = {item.field: item for item in result.report.issues}
    connection = result.models.chat.connections[0]

    assert {
        "llm.openai.api_key",
        "llm.openai.model",
        "llm.openai.base_url",
        "llm.openai.auth_mode",
    }.issubset(issues)
    diagnostic_keys = [(item.field, item.code, item.reason) for item in result.report.issues]
    assert len(diagnostic_keys) == len(set(diagnostic_keys))
    assert all(issues[field].code == "invalid_legacy_value" for field in issues)
    malformed_credential = next(
        item
        for item in result.report.issues
        if item.field == "llm.openai.api_key" and item.reason == "legacy_credential_must_be_string"
    )
    assert malformed_credential.credential_configured is True
    assert connection.credential.source == "none"
    assert connection.base_url == ""
    assert "credential-object-secret" not in repr(result)
    assert "model-secret" not in repr(result)


def test_exact_integer_fields_reject_booleans_and_fractional_floats() -> None:
    raw = legacy_provider("ollama")
    raw["concurrency"] = 1.5
    raw["timeout"] = True
    cast("dict[str, object]", raw["ollama"])["num_ctx"] = 2048.5
    raw["embedding"] = {
        "provider": "ollama",
        "model": "bge-m3",
        "output_dimensionality": 768.5,
        "fallback_enabled": False,
    }

    result = _migrate(raw)
    fields = {item.field for item in result.report.issues}

    assert result.models.chat.concurrency == 4
    assert result.models.chat.timeout_seconds == 300
    assert result.models.chat.connections[0].num_ctx == 0
    assert result.models.embedding.settings.output_dimensionality == 1024
    assert {
        "llm.concurrency",
        "llm.timeout",
        "llm.ollama.num_ctx",
        "llm.embedding.output_dimensionality",
    }.issubset(fields)


def test_malformed_unknown_provider_data_preserves_only_safe_metadata() -> None:
    result = _migrate(
        {
            "default_provider": "vendor-x",
            "vendor-x": "unknown-provider-secret",
        }
    )
    issue = next(item for item in result.report.issues if item.code == "unknown_provider")

    assert issue.field == "llm.default_provider"
    assert issue.provider == "vendor-x"
    assert issue.credential_configured is True
    assert "unknown-provider-secret" not in repr(result)


def test_legacy_ids_and_revision_are_stable_and_globally_unique() -> None:
    legacy = legacy_with_three_configured_providers()
    legacy["embedding"] = {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "api_key": "embedding-secret",
        "output_dimensionality": 1536,
        "similarity_threshold": 0.8,
        "multimodal_enabled": False,
    }

    first = _migrate(legacy)
    second = _migrate(legacy)
    ids = [item.id for item in first.models.chat.connections] + [
        item.id for item in first.models.embedding.providers
    ]

    assert first.models == second.models
    assert first.report == second.report
    assert compute_model_revision(first.models) == compute_model_revision(second.models)
    assert len(ids) == len(set(ids))
    assert ids == ["legacy-chat-deepseek", "legacy-chat-openrouter", "legacy-embedding-openai"]


def test_migration_issue_is_a_secret_free_public_value() -> None:
    result = _migrate(legacy_with_three_configured_providers())
    issue = result.report.issues[0]
    public = asdict(issue)

    assert set(public) == {
        "id",
        "code",
        "field",
        "provider",
        "credential_configured",
        "reason",
        "severity",
        "allowed_actions",
    }
    assert "openai-secret" not in repr(public)
    assert "openai-secret" not in repr(result.report)


def test_unrouted_credential_can_be_inserted_at_an_explicit_one_based_position() -> None:
    result = _migrate(legacy_with_three_configured_providers())
    issue = next(issue for issue in result.report.issues if issue.code == "unrouted_credential")

    resolved = _apply(
        result,
        {issue.id: _resolution("add_to_chat_route", position=2)},
    )

    assert [item.preset for item in resolved.chat.connections] == [
        "deepseek",
        "openai",
        "openrouter",
    ]
    assert validate_model_config(resolved, connection_type_registry()) == []


def test_unrouted_credential_can_be_acknowledged_for_backup_removal() -> None:
    result = _migrate(legacy_with_three_configured_providers())
    issue = next(issue for issue in result.report.issues if issue.code == "unrouted_credential")

    resolved = _apply(
        result,
        {issue.id: _resolution("confirm_remove_after_backup")},
    )

    assert resolved == result.models


def test_backup_removal_drops_an_active_routed_connection_with_missing_credential() -> None:
    result = _migrate(
        {
            "default_provider": "deepseek",
            "fallback_provider": "openai",
            "deepseek": {
                "api_key": "deepseek-secret",
                "model": "deepseek-v4-flash",
            },
            "openai": {"api_key": "", "model": "gpt-5-nano"},
        }
    )
    issue = next(item for item in result.report.issues if item.field == "llm.openai.api_key")

    resolved = _apply(
        result,
        {issue.id: _resolution("confirm_remove_after_backup")},
    )

    assert [item.preset for item in resolved.chat.connections] == ["deepseek"]
    assert validate_model_config(resolved, connection_type_registry()) == []


def test_backup_removal_fails_closed_when_it_would_remove_the_only_chat_connection() -> None:
    result = _migrate(
        {
            "default_provider": "openai",
            "openai": {"api_key": "", "model": "gpt-5-nano"},
        }
    )
    issue = next(item for item in result.report.issues if item.field == "llm.openai.api_key")

    with pytest.raises(MigrationResolutionError):
        _apply(
            result,
            {issue.id: _resolution("confirm_remove_after_backup")},
        )


def test_backup_removal_disables_an_unusable_active_embedding_provider() -> None:
    result = _migrate(
        {
            "default_provider": "deepseek",
            "deepseek": {
                "api_key": "deepseek-secret",
                "model": "deepseek-v4-flash",
            },
            "embedding": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "api_key": "",
                "output_dimensionality": 1536,
                "fallback_enabled": False,
                "multimodal_enabled": False,
            },
        }
    )
    issue = next(
        item
        for item in result.report.issues
        if item.reason == "configured_embedding_provider_has_no_usable_configuration"
    )

    resolved = _apply(
        result,
        {issue.id: _resolution("confirm_remove_after_backup")},
    )

    assert resolved.embedding.enabled is False
    assert resolved.embedding.providers == ()
    assert validate_model_config(resolved, connection_type_registry()) == []


def test_chat_resolution_rejects_ten_item_route_overflow() -> None:
    result = _migrate(legacy_with_three_configured_providers())
    issue = next(issue for issue in result.report.issues if issue.code == "unrouted_credential")
    template = result.models.chat.connections[0]
    full_route = tuple(replace(template, id=f"existing-{index}") for index in range(10))
    full_result = replace(
        result,
        models=replace(
            result.models,
            chat=replace(result.models.chat, connections=full_route),
        ),
    )

    with pytest.raises(MigrationResolutionError):
        _apply(
            full_result,
            {issue.id: _resolution("add_to_chat_route", position=10)},
        )


def test_embedding_mismatch_resolution_requires_and_applies_explicit_shared_settings() -> None:
    result = _migrate(legacy_embedding(primary_model="bge-m3"))
    issue = next(
        issue for issue in result.report.issues if issue.code == "embedding_space_mismatch"
    )
    settings = EmbeddingModelSettings(
        model="text-embedding-3-small",
        output_dimensionality=1536,
        similarity_threshold=0.77,
        multimodal_enabled=False,
    )

    resolved = _apply(
        result,
        {
            issue.id: _resolution(
                "apply_shared_embedding_settings",
                embedding_settings=settings,
            )
        },
    )

    assert resolved.embedding.settings == settings
    assert [provider.preset for provider in resolved.embedding.providers] == ["", "openai"]


def test_embedding_mismatch_can_remove_the_pending_fallback() -> None:
    result = _migrate(legacy_embedding(primary_model="bge-m3"))
    issue = next(
        issue for issue in result.report.issues if issue.code == "embedding_space_mismatch"
    )

    resolved = _apply(
        result,
        {issue.id: _resolution("remove_embedding_fallback")},
    )

    assert resolved == result.models


def test_shared_settings_cannot_activate_an_unusable_embedding_fallback() -> None:
    legacy = legacy_embedding(primary_model="bge-m3")
    legacy["default_provider"] = "deepseek"
    legacy["deepseek"] = {
        "api_key": "deepseek-secret",
        "model": "deepseek-v4-flash",
    }
    cast("dict[str, object]", legacy["openai"])["api_key"] = ""
    result = _migrate(legacy)
    issue = next(item for item in result.report.issues if item.code == "embedding_space_mismatch")
    settings = EmbeddingModelSettings(
        model="text-embedding-3-small",
        output_dimensionality=1536,
        similarity_threshold=0.77,
        multimodal_enabled=False,
    )

    with pytest.raises(MigrationResolutionError):
        _apply(
            result,
            {
                issue.id: _resolution(
                    "apply_shared_embedding_settings",
                    embedding_settings=settings,
                )
            },
        )


def test_resolution_rejects_a_structurally_invalid_final_model() -> None:
    result = _migrate(legacy_provider("deepseek"))
    connection = replace(result.models.chat.connections[0], model="")
    invalid = replace(
        result,
        models=replace(
            result.models,
            chat=replace(result.models.chat, connections=(connection,)),
        ),
        report=MigrationReport(),
    )

    with pytest.raises(MigrationResolutionError):
        _apply(invalid, {})


@pytest.mark.parametrize(
    "choice_factory",
    [
        lambda: {},
        lambda: {"nonexistent-issue": _resolution("confirm_remove_after_backup")},
    ],
)
def test_missing_or_extra_resolution_choices_are_blocking(
    choice_factory: Callable[[], dict[str, MigrationResolution]],
) -> None:
    result = _migrate(legacy_with_three_configured_providers())

    with pytest.raises(MigrationResolutionError):
        _apply(result, choice_factory())


@pytest.mark.parametrize(
    ("action", "position"),
    [
        pytest.param("cancel", None, id="cancel"),
        pytest.param("remove_embedding_fallback", None, id="wrong-issue-action"),
        pytest.param("add_to_chat_route", None, id="missing-position"),
        pytest.param("add_to_chat_route", 0, id="invalid-position"),
    ],
)
def test_unknown_or_invalid_unrouted_resolution_is_blocking_and_value_free(
    action: str,
    position: int | None,
) -> None:
    result = _migrate(legacy_with_three_configured_providers())
    issue = next(issue for issue in result.report.issues if issue.code == "unrouted_credential")
    resolution = _resolution(action, position=position)

    with pytest.raises(MigrationResolutionError) as raised:
        _apply(result, {issue.id: resolution})

    assert "openai-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("settings",),
    [
        pytest.param(None, id="missing-settings"),
        pytest.param(
            EmbeddingModelSettings(model="", output_dimensionality=-1),
            id="invalid-settings",
        ),
    ],
)
def test_embedding_resolution_rejects_missing_or_invalid_settings(
    settings: EmbeddingModelSettings | None,
) -> None:
    result = _migrate(legacy_embedding(primary_model="bge-m3"))
    issue = next(
        issue for issue in result.report.issues if issue.code == "embedding_space_mismatch"
    )
    resolution = _resolution(
        "apply_shared_embedding_settings",
        embedding_settings=settings,
    )

    with pytest.raises(MigrationResolutionError):
        _apply(result, {issue.id: resolution})


def test_module_override_acceptance_is_closed_and_cancel_remains_blocking() -> None:
    result = _migrate(legacy_with_module_override("evaluation", "openai"))
    issue = next(issue for issue in result.report.issues if issue.code == "module_override_removed")

    assert _apply(result, {issue.id: _resolution("accept_global_route")}) == result.models
    with pytest.raises(MigrationResolutionError):
        _apply(result, {issue.id: _resolution("cancel")})
    with pytest.raises(MigrationResolutionError):
        _apply(result, {issue.id: _resolution("confirm_remove_after_backup")})
