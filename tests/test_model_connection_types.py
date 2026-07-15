from __future__ import annotations

import json

import pytest

from openbiliclaw.model_config import (
    ChatConnection,
    CredentialConfig,
    apply_preset_defaults,
    connection_type_registry,
)


def test_registry_groups_protocol_local_and_oauth_types() -> None:
    descriptors = connection_type_registry().public_descriptors()
    by_id = {item["id"]: item for item in descriptors}
    assert by_id["openai_compatible"]["presets"] == [
        "openai",
        "deepseek",
        "openrouter",
        "custom",
    ]
    assert by_id["anthropic_compatible"]["presets"] == ["anthropic", "custom"]
    assert by_id["codex_oauth"]["category"] == "oauth"
    assert by_id["dashscope_api"]["capabilities"] == ["embedding"]


def test_deepseek_preset_is_not_an_embedding_choice() -> None:
    registry = connection_type_registry()
    assert registry.presets_for("openai_compatible", "chat") == (
        "openai",
        "deepseek",
        "openrouter",
        "custom",
    )
    assert registry.presets_for("openai_compatible", "embedding") == ("openai", "custom")


def test_public_descriptors_are_json_safe_and_contain_rendering_metadata() -> None:
    descriptors = connection_type_registry().public_descriptors()
    encoded = json.dumps(descriptors, sort_keys=True)
    assert "openbiliclaw." not in encoded
    assert "<class" not in encoded
    assert "callable" not in encoded
    for descriptor in descriptors:
        assert isinstance(descriptor["label"], str)
        assert isinstance(descriptor["help"], str)
        assert isinstance(descriptor["fields"], list)
        assert all("name" in field and "label" in field for field in descriptor["fields"])
        assert isinstance(descriptor["preset_definitions"], list)


@pytest.mark.parametrize(
    ("capability", "expected_ids"),
    [
        (
            "chat",
            (
                "openai_compatible",
                "anthropic_compatible",
                "gemini_api",
                "ollama",
                "codex_oauth",
            ),
        ),
        (
            "embedding",
            ("openai_compatible", "gemini_api", "dashscope_api", "ollama"),
        ),
    ],
)
def test_registry_filters_connection_types_by_capability(
    capability: str,
    expected_ids: tuple[str, ...],
) -> None:
    registry = connection_type_registry()
    assert tuple(item.id for item in registry.for_capability(capability)) == expected_ids


def test_preset_defaults_only_fill_blank_untouched_fields() -> None:
    registry = connection_type_registry()
    preset = registry.preset("openai_compatible", "deepseek")
    original = ChatConnection(
        id="deepseek",
        name="DeepSeek",
        type="openai_compatible",
        preset="deepseek",
        model="my-custom-model",
        credential=CredentialConfig(source="env", value="DEEPSEEK_API_KEY"),
    )
    updated = apply_preset_defaults(original, preset, frozenset({"reasoning_effort"}))
    assert updated is not original
    assert original.base_url == ""
    assert updated.base_url == "https://api.deepseek.com"
    assert updated.model == "my-custom-model"
    assert updated.reasoning_effort == ""


def test_preset_defaults_and_registry_collections_are_immutable() -> None:
    registry = connection_type_registry()
    definition = registry.definition("openai_compatible")
    preset = registry.preset("openai_compatible", "deepseek")
    assert isinstance(registry.definitions, tuple)
    assert isinstance(definition.capabilities, tuple)
    assert isinstance(definition.fields, tuple)
    assert isinstance(definition.presets, tuple)
    with pytest.raises(TypeError):
        preset.defaults["base_url"] = "https://attacker.example"  # type: ignore[index]
