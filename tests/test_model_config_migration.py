"""Tests for deterministic, read-only migration of legacy ``[llm]`` data."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

import pytest

from openbiliclaw import model_config as model_config_module
from openbiliclaw.model_config import (
    CredentialConfig,
    EmbeddingModelSettings,
    compute_model_revision,
)


def _migrate(raw: dict[str, Any], env: dict[str, str] | None = None) -> Any:
    migrate = getattr(model_config_module, "migrate_legacy_llm", None)
    assert callable(migrate), "migrate_legacy_llm must be implemented"
    return migrate(raw, env or {})


def _resolution(
    action: str,
    *,
    position: int | None = None,
    embedding_settings: EmbeddingModelSettings | None = None,
) -> Any:
    resolution_type = getattr(model_config_module, "MigrationResolution", None)
    assert isinstance(resolution_type, type), "MigrationResolution must be implemented"
    return resolution_type(
        action=action,
        position=position,
        embedding_settings=embedding_settings,
    )


def _apply(result: Any, choices: dict[str, Any]) -> Any:
    apply_resolutions = getattr(model_config_module, "apply_migration_resolutions", None)
    assert callable(apply_resolutions), "apply_migration_resolutions must be implemented"
    return apply_resolutions(result, choices)


def _resolution_error_type() -> type[ValueError]:
    error_type = getattr(model_config_module, "MigrationResolutionError", None)
    assert isinstance(error_type, type) and issubclass(error_type, ValueError)
    return error_type


_CHAT_MODELS = {
    "openai": "gpt-5-nano",
    "deepseek": "deepseek-v4-flash",
    "openrouter": "openai/gpt-5-nano",
    "openai_compatible": "custom-chat",
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "ollama": "qwen2.5:7b",
}


def legacy_provider(provider: str, *, base_url: str = "") -> dict[str, Any]:
    provider_config: dict[str, Any] = {
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
    legacy: dict[str, Any], expected_type: str, expected_preset: str
) -> None:
    result = _migrate(legacy)
    connection = result.models.chat.connections[0]

    assert (connection.type, connection.preset) == (expected_type, expected_preset)


def test_official_provider_hosts_keep_official_presets() -> None:
    openai = legacy_provider("openai", base_url="https://API.OpenAI.com/v1/")
    anthropic = legacy_provider("claude", base_url="https://api.anthropic.com/v1")

    assert _migrate(openai).models.chat.connections[0].preset == "openai"
    assert _migrate(anthropic).models.chat.connections[0].preset == "anthropic"


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


def legacy_with_three_configured_providers() -> dict[str, Any]:
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
) -> dict[str, Any]:
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


def legacy_with_module_override(module: str, provider: str) -> dict[str, Any]:
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


def test_unrouted_credential_can_be_acknowledged_for_backup_removal() -> None:
    result = _migrate(legacy_with_three_configured_providers())
    issue = next(issue for issue in result.report.issues if issue.code == "unrouted_credential")

    resolved = _apply(
        result,
        {issue.id: _resolution("confirm_remove_after_backup")},
    )

    assert resolved == result.models


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

    with pytest.raises(_resolution_error_type()):
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


@pytest.mark.parametrize(
    "choice_factory",
    [
        lambda: {},
        lambda: {"nonexistent-issue": _resolution("confirm_remove_after_backup")},
    ],
)
def test_missing_or_extra_resolution_choices_are_blocking(
    choice_factory: Any,
) -> None:
    result = _migrate(legacy_with_three_configured_providers())

    with pytest.raises(_resolution_error_type()):
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

    with pytest.raises(_resolution_error_type()) as raised:
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

    with pytest.raises(_resolution_error_type()):
        _apply(result, {issue.id: resolution})


def test_module_override_acceptance_is_closed_and_cancel_remains_blocking() -> None:
    result = _migrate(legacy_with_module_override("evaluation", "openai"))
    issue = next(issue for issue in result.report.issues if issue.code == "module_override_removed")

    assert _apply(result, {issue.id: _resolution("accept_global_route")}) == result.models
    with pytest.raises(_resolution_error_type()):
        _apply(result, {issue.id: _resolution("cancel")})
    with pytest.raises(_resolution_error_type()):
        _apply(result, {issue.id: _resolution("confirm_remove_after_backup")})
