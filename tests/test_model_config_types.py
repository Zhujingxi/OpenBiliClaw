from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError, asdict, replace
from typing import cast

import pytest

from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    CredentialSource,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
    connection_type_registry,
    default_model_config,
    validate_model_config,
)


def chat_connection(
    connection_id: str,
    *,
    connection_type: str = "openai_compatible",
    preset: str = "custom",
    credential: CredentialConfig | None = None,
    **overrides: object,
) -> ChatConnection:
    values: dict[str, object] = {
        "id": connection_id,
        "name": connection_id or "Unnamed",
        "type": connection_type,
        "preset": preset,
        "model": "test-chat-model",
        "base_url": "https://gateway.example/v1",
        "credential": credential or CredentialConfig(source="env", value="TEST_API_KEY"),
        "api_mode": "chat_completions",
    }
    values.update(overrides)
    return ChatConnection(**values)  # type: ignore[arg-type]


def model_config(
    *,
    chat_ids: tuple[str, ...] = ("primary",),
    chat_count: int | None = None,
    embedding_enabled: bool = False,
    embedding_providers: tuple[object, ...] = (),
) -> ModelConfig:
    if chat_count is not None:
        chat_ids = tuple(f"chat-{index}" for index in range(chat_count))
    return ModelConfig(
        schema_version=1,
        chat=ChatRouteConfig(connections=tuple(chat_connection(item) for item in chat_ids)),
        embedding=EmbeddingRouteConfig(
            enabled=embedding_enabled,
            settings=EmbeddingModelSettings(model="text-embedding-test"),
            providers=cast("tuple[EmbeddingProviderConfig, ...]", embedding_providers),
        ),
    )


def issue_codes(config: ModelConfig) -> set[str]:
    return {issue.code for issue in validate_model_config(config, connection_type_registry())}


def _with_numeric_value(config: ModelConfig, field: str, value: object) -> ModelConfig:
    if field == "concurrency":
        return replace(config, chat=replace(config.chat, concurrency=value))  # type: ignore[arg-type]
    if field == "timeout_seconds":
        return replace(config, chat=replace(config.chat, timeout_seconds=value))  # type: ignore[arg-type]
    settings = config.embedding.settings
    if field == "output_dimensionality":
        settings = replace(settings, output_dimensionality=value)  # type: ignore[arg-type]
    else:
        settings = replace(settings, similarity_threshold=value)  # type: ignore[arg-type]
    return replace(config, embedding=replace(config.embedding, settings=settings))


@pytest.mark.parametrize(
    ("field", "value", "path", "code"),
    [
        ("concurrency", 0, "models.chat.concurrency", "invalid_chat_concurrency"),
        ("concurrency", 17, "models.chat.concurrency", "invalid_chat_concurrency"),
        ("concurrency", True, "models.chat.concurrency", "invalid_chat_concurrency"),
        ("concurrency", 1.5, "models.chat.concurrency", "invalid_chat_concurrency"),
        ("timeout_seconds", 9, "models.chat.timeout_seconds", "invalid_chat_timeout"),
        ("timeout_seconds", True, "models.chat.timeout_seconds", "invalid_chat_timeout"),
        ("timeout_seconds", 10.5, "models.chat.timeout_seconds", "invalid_chat_timeout"),
        (
            "output_dimensionality",
            -1,
            "models.embedding.settings.output_dimensionality",
            "invalid_embedding_output_dimensionality",
        ),
        (
            "output_dimensionality",
            True,
            "models.embedding.settings.output_dimensionality",
            "invalid_embedding_output_dimensionality",
        ),
        (
            "output_dimensionality",
            1.5,
            "models.embedding.settings.output_dimensionality",
            "invalid_embedding_output_dimensionality",
        ),
        (
            "similarity_threshold",
            -0.01,
            "models.embedding.settings.similarity_threshold",
            "invalid_embedding_similarity_threshold",
        ),
        (
            "similarity_threshold",
            1.01,
            "models.embedding.settings.similarity_threshold",
            "invalid_embedding_similarity_threshold",
        ),
        (
            "similarity_threshold",
            True,
            "models.embedding.settings.similarity_threshold",
            "invalid_embedding_similarity_threshold",
        ),
        (
            "similarity_threshold",
            float("nan"),
            "models.embedding.settings.similarity_threshold",
            "invalid_embedding_similarity_threshold",
        ),
        (
            "similarity_threshold",
            float("inf"),
            "models.embedding.settings.similarity_threshold",
            "invalid_embedding_similarity_threshold",
        ),
        (
            "similarity_threshold",
            10**1000,
            "models.embedding.settings.similarity_threshold",
            "invalid_embedding_similarity_threshold",
        ),
    ],
)
def test_domain_validation_rejects_invalid_route_numeric_values(
    field: str,
    value: object,
    path: str,
    code: str,
) -> None:
    config = _with_numeric_value(model_config(), field, value)

    issues = validate_model_config(config, connection_type_registry())

    assert [(issue.path, issue.code) for issue in issues] == [(path, code)]


def test_domain_validation_accepts_numeric_boundaries() -> None:
    config = model_config()
    config = replace(config, chat=replace(config.chat, concurrency=1, timeout_seconds=10))
    config = replace(
        config,
        embedding=replace(
            config.embedding,
            settings=replace(
                config.embedding.settings,
                output_dimensionality=0,
                similarity_threshold=0.0,
            ),
        ),
    )
    assert validate_model_config(config, connection_type_registry()) == []

    upper = replace(config, chat=replace(config.chat, concurrency=16))
    upper = replace(
        upper,
        embedding=replace(
            upper.embedding,
            settings=replace(upper.embedding.settings, similarity_threshold=1.0),
        ),
    )
    assert validate_model_config(upper, connection_type_registry()) == []


def test_domain_validation_bounds_deepseek_reasoning_effort() -> None:
    deepseek = chat_connection(
        "deepseek",
        preset="deepseek",
        base_url="https://api.deepseek.com",
        reasoning_effort="",
    )
    config = replace(model_config(), chat=ChatRouteConfig(connections=(deepseek,)))

    assert validate_model_config(config, connection_type_registry()) == []

    invalid = replace(deepseek, reasoning_effort="off")
    issues = validate_model_config(
        replace(config, chat=ChatRouteConfig(connections=(invalid,))),
        connection_type_registry(),
    )
    assert [
        (issue.path, issue.code, issue.connection_id)
        for issue in issues
    ] == [
        (
            "models.chat.connections[0].reasoning_effort",
            "invalid_connection_field_choice",
            "deepseek",
        )
    ]


def test_domain_validation_rejects_negative_ollama_num_ctx_with_connection_id() -> None:
    connection = chat_connection(
        "local-ollama",
        connection_type="ollama",
        preset="",
        credential=CredentialConfig(),
        base_url="http://127.0.0.1:11434/v1",
        api_mode="",
        num_ctx=-1,
    )
    config = replace(model_config(), chat=ChatRouteConfig(connections=(connection,)))

    issues = validate_model_config(config, connection_type_registry())

    assert [
        (issue.path, issue.code, issue.connection_id)
        for issue in issues
    ] == [
        (
            "models.chat.connections[0].num_ctx",
            "invalid_chat_num_ctx",
            "local-ollama",
        )
    ]


def test_chat_roles_are_derived_only_from_order() -> None:
    config = model_config(chat_ids=("first", "second", "third"))
    assert [config.chat.role_at(i) for i in range(3)] == [
        "primary",
        "fallback_1",
        "fallback_2",
    ]
    assert "priority" not in asdict(config.chat.connections[0])
    assert "fallback_enabled" not in asdict(config.chat.connections[0])


@pytest.mark.parametrize("count,valid", [(0, False), (1, True), (10, True), (11, False)])
def test_chat_route_size_is_one_through_ten(count: int, valid: bool) -> None:
    issues = validate_model_config(model_config(chat_count=count), connection_type_registry())
    has_count_issue = any(issue.code == "chat_connection_count" for issue in issues)
    assert has_count_issue is not valid


def test_embedding_provider_has_no_model_slot() -> None:
    fields = {field.name for field in dataclasses.fields(EmbeddingProviderConfig)}
    assert "model" not in fields


def test_domain_values_and_ordered_collections_are_immutable() -> None:
    config = model_config(chat_ids=("first", "second"))
    assert isinstance(config.chat.connections, tuple)
    assert isinstance(config.embedding.providers, tuple)
    with pytest.raises(FrozenInstanceError):
        config.chat.concurrency = 8  # type: ignore[misc]


def test_credential_value_is_hidden_from_repr() -> None:
    secret = "sk-super-secret"
    credential = CredentialConfig(source="inline", value=secret)
    connection = chat_connection("private", credential=credential)
    assert secret not in repr(credential)
    assert secret not in repr(connection)


@pytest.mark.parametrize("count,valid", [(0, False), (1, True), (10, True), (11, False)])
def test_enabled_embedding_route_size_is_one_through_ten(count: int, valid: bool) -> None:
    providers = tuple(
        EmbeddingProviderConfig(
            id=f"embedding-{index}",
            name=f"Embedding {index}",
            type="ollama",
            base_url="http://127.0.0.1:11434/v1",
        )
        for index in range(count)
    )
    config = model_config(embedding_enabled=True, embedding_providers=providers)
    assert ("embedding_provider_count" not in issue_codes(config)) is valid


def test_valid_typed_embedding_provider_has_no_validation_issues() -> None:
    provider = EmbeddingProviderConfig(
        id="ollama-embedding",
        name="Ollama Embedding",
        type="ollama",
        base_url="http://127.0.0.1:11434/v1",
    )
    config = model_config(
        embedding_enabled=True,
        embedding_providers=(provider,),
    )
    assert validate_model_config(config, connection_type_registry()) == []


def test_connection_ids_are_required_and_unique_across_both_routes() -> None:
    blank = model_config(chat_ids=("",))
    assert "blank_connection_id" in issue_codes(blank)

    duplicate_provider = EmbeddingProviderConfig(
        id="primary",
        name="Duplicate",
        type="ollama",
        base_url="http://127.0.0.1:11434/v1",
    )
    duplicate = model_config(
        embedding_enabled=True,
        embedding_providers=(duplicate_provider,),
    )
    assert "duplicate_connection_id" in issue_codes(duplicate)


def test_validation_rejects_unknown_types_and_presets() -> None:
    unknown_type = replace(
        model_config().chat.connections[0],
        type="not_registered",
        preset="",
    )
    config = replace(model_config(), chat=ChatRouteConfig(connections=(unknown_type,)))
    assert "unknown_connection_type" in issue_codes(config)

    unknown_preset = replace(model_config().chat.connections[0], preset="not_registered")
    config = replace(model_config(), chat=ChatRouteConfig(connections=(unknown_preset,)))
    assert "unknown_preset" in issue_codes(config)


def test_validation_rejects_capability_and_type_specific_field_mismatches() -> None:
    embedding_only_chat = chat_connection(
        "dashscope-chat",
        connection_type="dashscope_api",
        preset="",
        api_mode="",
    )
    config = replace(model_config(), chat=ChatRouteConfig(connections=(embedding_only_chat,)))
    assert "unsupported_capability" in issue_codes(config)

    ollama_with_api_mode = chat_connection(
        "local",
        connection_type="ollama",
        preset="",
        credential=CredentialConfig(),
    )
    config = replace(model_config(), chat=ChatRouteConfig(connections=(ollama_with_api_mode,)))
    assert "illegal_connection_field" in issue_codes(config)


def test_validation_rejects_invalid_credential_sources() -> None:
    invalid_source = cast("CredentialSource", "keychain")
    connection = chat_connection(
        "invalid-credential",
        credential=CredentialConfig(source=invalid_source, value="entry"),
    )
    config = replace(model_config(), chat=ChatRouteConfig(connections=(connection,)))
    assert "invalid_credential_source" in issue_codes(config)


def test_validation_rejects_model_inside_raw_embedding_provider() -> None:
    raw_provider: object = {
        "id": "raw",
        "name": "Raw provider",
        "type": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "must-live-on-shared-settings",
    }
    config = model_config(
        embedding_enabled=True,
        embedding_providers=(raw_provider,),
    )
    assert "embedding_provider_model" in issue_codes(config)


def test_default_model_config_is_an_editable_deepseek_route() -> None:
    config = default_model_config()
    assert config.schema_version == 1
    assert config.chat.concurrency == 4
    assert config.chat.timeout_seconds == 300
    assert len(config.chat.connections) == 1
    assert config.chat.connections[0].type == "openai_compatible"
    assert config.chat.connections[0].preset == "deepseek"
    assert config.chat.connections[0].model == "deepseek-v4-flash"
    assert config.embedding.enabled is False
    assert config.embedding.providers == ()
