"""Tests for native model configuration persistence and revisions."""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import openbiliclaw.model_config as model_config_module
from openbiliclaw.config import Config, ConfigError, load_config, save_config
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
)

NATIVE_MODELS_TOML = """
[models]
schema_version = 1

[models.chat]
concurrency = 5
timeout_seconds = 240

[[models.chat.connections]]
id = "deepseek-main"
name = "DeepSeek Main"
type = "openai_compatible"
preset = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key = "sk-native-inline"
api_mode = "chat_completions"
reasoning_effort = "max"

[[models.chat.connections]]
id = "router"
name = "OpenRouter"
type = "openai_compatible"
preset = "openrouter"
model = "openai/gpt-5-nano"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
api_mode = "chat_completions"
http_referer = "https://openbiliclaw.local"
x_title = "OpenBiliClaw"

[models.embedding]
enabled = true

[models.embedding.settings]
model = "text-embedding-3-small"
output_dimensionality = 1536
similarity_threshold = 0.81
multimodal_enabled = false

[[models.embedding.providers]]
id = "embedding-main"
name = "Embedding API"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
""".strip()


LEGACY_WITH_UNKNOWN_PROVIDER_KEY = """
[llm]
default_provider = "deepseek"
concurrency = 4
timeout = 300
vendor_extension = "keep-me"
priority_labels = ["primary", "fallback"]

[llm.deepseek]
api_key = "sk-legacy-inline"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
reasoning_effort = "max"
vendor_option = "keep-provider-option"

[llm.vendor]
enabled = true

[llm.vendor.nested]
mode = "custom"

[[llm.vendor.routes]]
name = "first"
secret = "route-inline-secret"

[[llm.vendor.routes]]
name = "second"
weights = [2, 1]
""".strip()


def _parse_model_config(raw: dict[str, Any]) -> ModelConfig:
    parser = getattr(model_config_module, "parse_model_config", None)
    assert callable(parser), "parse_model_config must be implemented"
    return parser(raw)


def _render_model_config(config: ModelConfig) -> list[str]:
    renderer = getattr(model_config_module, "render_model_config", None)
    assert callable(renderer), "render_model_config must be implemented"
    return renderer(config)


def _compute_model_revision(config: ModelConfig) -> str:
    compute = getattr(model_config_module, "compute_model_revision", None)
    assert callable(compute), "compute_model_revision must be implemented"
    return compute(config)


def _parse_error_type() -> type[ValueError]:
    error_type = getattr(model_config_module, "ModelConfigParseError", None)
    assert isinstance(error_type, type) and issubclass(error_type, ValueError)
    return error_type


def native_models_raw() -> dict[str, Any]:
    return tomllib.loads(NATIVE_MODELS_TOML)["models"]


def model_config(
    *,
    secret: str = "sk-inline",
    chat_ids: tuple[str, ...] = ("a", "b"),
) -> ModelConfig:
    return ModelConfig(
        schema_version=1,
        chat=ChatRouteConfig(
            connections=tuple(
                ChatConnection(
                    id=connection_id,
                    name=f"Connection {connection_id}",
                    type="openai_compatible",
                    preset="custom",
                    model=f"model-{connection_id}",
                    base_url=f"https://{connection_id}.example/v1",
                    credential=CredentialConfig(source="inline", value=secret),
                    api_mode="chat_completions",
                )
                for connection_id in chat_ids
            ),
            concurrency=3,
            timeout_seconds=90,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=True,
            settings=EmbeddingModelSettings(
                model="embedding-model",
                output_dimensionality=768,
                similarity_threshold=0.75,
                multimodal_enabled=True,
            ),
            providers=(
                EmbeddingProviderConfig(
                    id="embedding",
                    name="Embedding",
                    type="openai_compatible",
                    preset="custom",
                    base_url="https://embedding.example/v1",
                    credential=CredentialConfig(source="env", value="EMBEDDING_API_KEY"),
                ),
            ),
        ),
    )


def test_parse_model_config_preserves_order_and_normalizes_string_enums() -> None:
    raw = native_models_raw()
    first = raw["chat"]["connections"][0]
    first["type"] = " OpenAI_Compatible "
    first["preset"] = " DeepSeek "
    first["api_mode"] = " Chat_Completions "
    first["reasoning_effort"] = " MAX "

    parsed = _parse_model_config(raw)

    assert [item.id for item in parsed.chat.connections] == ["deepseek-main", "router"]
    assert parsed.chat.connections[0].type == "openai_compatible"
    assert parsed.chat.connections[0].preset == "deepseek"
    assert parsed.chat.connections[0].api_mode == "chat_completions"
    assert parsed.chat.connections[0].reasoning_effort == "max"
    assert parsed.chat.connections[0].credential == CredentialConfig(
        source="inline", value="sk-native-inline"
    )
    assert parsed.chat.connections[1].credential == CredentialConfig(
        source="env", value="OPENROUTER_API_KEY"
    )
    assert parsed.embedding.providers[0].credential == CredentialConfig(
        source="env", value="OPENAI_API_KEY"
    )


def test_parse_model_config_maps_credential_ref_to_oauth() -> None:
    raw = native_models_raw()
    connection = raw["chat"]["connections"][0]
    connection.pop("api_key")
    connection["type"] = "codex_oauth"
    connection["preset"] = ""
    connection["credential_ref"] = "codex"

    parsed = _parse_model_config(raw)

    assert parsed.chat.connections[0].credential == CredentialConfig(source="oauth", value="codex")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("stale_top_level",), True),
        (("chat", "stale_route_field"), "old"),
        (("chat", "connections", 0, "stale_provider_field"), "must-not-survive"),
        (("embedding", "settings", "stale_setting"), 1),
        (("embedding", "providers", 0, "model"), "must-be-shared"),
    ],
)
def test_unknown_native_field_is_blocking(path: tuple[object, ...], value: object) -> None:
    raw = native_models_raw()
    target: Any = raw
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    with pytest.raises(_parse_error_type(), match=str(path[-1])):
        _parse_model_config(raw)


@pytest.mark.parametrize("schema_version", [0, 2, "1", True])
def test_parse_model_config_accepts_only_integer_schema_version_one(
    schema_version: object,
) -> None:
    raw = native_models_raw()
    raw["schema_version"] = schema_version

    with pytest.raises(_parse_error_type(), match="schema_version"):
        _parse_model_config(raw)


def test_multiple_credential_sources_are_blocking_and_secret_safe() -> None:
    raw = native_models_raw()
    connection = raw["chat"]["connections"][0]
    connection["api_key_env"] = "SECOND_SECRET_SOURCE"

    with pytest.raises(_parse_error_type()) as raised:
        _parse_model_config(raw)

    message = str(raised.value)
    assert "credential" in message
    assert "sk-native-inline" not in message
    assert "SECOND_SECRET_SOURCE" not in message


def test_render_model_config_round_trips_and_is_deterministic() -> None:
    config = model_config()

    first = _render_model_config(config)
    second = _render_model_config(config)
    reparsed = _parse_model_config(tomllib.loads("\n".join(first))["models"])

    assert first == second
    assert reparsed == config
    assert first.index("[[models.chat.connections]]") < first.index(
        "[[models.embedding.providers]]"
    )


@pytest.mark.parametrize("schema_version", [1.0, True])
def test_render_model_config_requires_exact_integer_schema_version_one(
    schema_version: object,
) -> None:
    config = replace(model_config(), schema_version=schema_version)

    with pytest.raises(ValueError, match="schema_version"):
        _render_model_config(config)


def test_render_model_config_flattens_each_credential_source() -> None:
    config = model_config(chat_ids=("inline", "env", "oauth"))
    connections = (
        replace(
            config.chat.connections[0],
            credential=CredentialConfig(source="inline", value="sk-inline"),
        ),
        replace(
            config.chat.connections[1],
            credential=CredentialConfig(source="env", value="MODEL_API_KEY"),
        ),
        replace(
            config.chat.connections[2],
            type="codex_oauth",
            preset="",
            credential=CredentialConfig(source="oauth", value="codex"),
        ),
    )
    config = replace(config, chat=replace(config.chat, connections=connections))

    rendered = "\n".join(_render_model_config(config))

    assert 'api_key = "sk-inline"' in rendered
    assert 'api_key_env = "MODEL_API_KEY"' in rendered
    assert 'credential_ref = "codex"' in rendered


def test_render_model_config_never_writes_empty_inline_secret_placeholder() -> None:
    config = model_config(chat_ids=("empty",))
    connection = replace(
        config.chat.connections[0],
        credential=CredentialConfig(source="inline", value=""),
    )
    config = replace(config, chat=replace(config.chat, connections=(connection,)))

    rendered = "\n".join(_render_model_config(config))

    assert 'api_key = ""' not in rendered


def test_authoritative_native_render_escapes_forbidden_toml_controls() -> None:
    config = model_config(chat_ids=("control",))
    forbidden_controls = "\x00\x08\t\n\x0b\x0c\r\x1f\x7f"
    connection = replace(
        config.chat.connections[0],
        name=f"Native{forbidden_controls}Name",
    )
    config = replace(config, chat=replace(config.chat, connections=(connection,)))

    rendered = "\n".join(_render_model_config(config))
    reparsed = tomllib.loads(rendered)

    assert "\\u007F" in rendered
    assert reparsed["models"]["chat"]["connections"][0]["name"] == connection.name


def test_revision_changes_when_inline_secret_changes_without_exposing_it() -> None:
    left = model_config(secret="sk-left")
    right = model_config(secret="sk-right")

    left_revision = _compute_model_revision(left)

    assert left_revision != _compute_model_revision(right)
    assert "sk-left" not in left_revision
    assert "sk-left" not in repr(left)


def test_reorder_changes_revision_but_not_connection_ids() -> None:
    config = model_config(chat_ids=("a", "b"))
    moved = replace(
        config,
        chat=replace(config.chat, connections=tuple(reversed(config.chat.connections))),
    )

    assert _compute_model_revision(config) != _compute_model_revision(moved)
    assert {item.id for item in config.chat.connections} == {
        item.id for item in moved.chat.connections
    }


def test_native_models_round_trip_keeps_order_and_secret_sources(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(NATIVE_MODELS_TOML, encoding="utf-8")

    loaded = load_config(path)

    assert [item.id for item in loaded.models.chat.connections] == [
        "deepseek-main",
        "router",
    ]
    assert loaded.model_meta.source == "native"
    save_config(loaded, path, models_authoritative=True)
    text = path.read_text(encoding="utf-8")
    reloaded = load_config(path)

    assert reloaded.models == loaded.models
    assert "[llm]" not in text


def test_unrelated_save_does_not_migrate_or_drop_legacy_model_data(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    before = tomllib.loads(path.read_text(encoding="utf-8"))["llm"]
    config = load_config(path)
    config.saved_sync.auto_sync_enabled = True

    save_config(config, path)

    text = path.read_text(encoding="utf-8")
    after = tomllib.loads(text)["llm"]
    assert "[llm]" in text
    assert "[models]" not in text
    assert 'vendor_extension = "keep-me"' in text
    assert after == before
    assert [route["name"] for route in after["vendor"]["routes"]] == ["first", "second"]
    assert after["deepseek"]["api_key"] == "sk-legacy-inline"


def test_raw_model_preservation_escapes_del_in_unknown_key_and_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[llm]\ndefault_provider = "deepseek"\n"vendor\\u007Fkey" = "value\\u007F"\n',
        encoding="utf-8",
    )
    loaded = load_config(path)

    save_config(loaded, path)

    rendered = path.read_text(encoding="utf-8")
    reparsed = tomllib.loads(rendered)
    assert rendered.count("\\u007F") >= 2
    assert reparsed["llm"]["vendor\x7fkey"] == "value\x7f"


def test_ordinary_save_aborts_when_existing_config_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = b'[llm]\napi_key = "secret-that-must-not-appear\n'
    path.write_bytes(original)

    with pytest.raises(ConfigError) as raised:
        save_config(Config(), path)

    assert path.read_bytes() == original
    assert "secret-that-must-not-appear" not in str(raised.value)


def test_ordinary_save_aborts_when_existing_config_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    original = b'[llm]\ndefault_provider = "deepseek"\n'
    path.write_bytes(original)
    original_open = Path.open

    def fail_target_reads(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if self == path and "r" in mode:
            raise OSError("secret-os-detail")
        return original_open(self, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_target_reads)
        with pytest.raises(ConfigError) as raised:
            save_config(Config(), path)

    assert path.read_bytes() == original
    assert "secret-os-detail" not in str(raised.value)


def test_ordinary_save_still_creates_a_genuinely_absent_destination(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    save_config(Config(), path)

    with path.open("rb") as handle:
        rendered = tomllib.load(handle)
    assert rendered["models"]["chat"]["connections"][0]["preset"] == "deepseek"
    assert "llm" not in rendered


def test_ordinary_save_preserves_model_absence_in_valid_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlanguage = "en"\n', encoding="utf-8")
    loaded = load_config(path)

    save_config(loaded, path)

    with path.open("rb") as handle:
        rendered = tomllib.load(handle)
    assert rendered["general"]["language"] == "en"
    assert "models" not in rendered
    assert "llm" not in rendered


def test_ordinary_save_keeps_legacy_model_table_read_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    loaded = load_config(path)
    loaded.models = replace(
        loaded.models,
        chat=replace(
            loaded.models.chat,
            connections=(replace(loaded.models.chat.connections[0], model="deepseek-updated"),),
        ),
    )

    save_config(loaded, path)

    with path.open("rb") as handle:
        llm = tomllib.load(handle)["llm"]
    assert llm["deepseek"]["model"] == "deepseek-v4-flash"
    assert llm["deepseek"]["vendor_option"] == "keep-provider-option"
    assert llm["vendor_extension"] == "keep-me"
    assert [route["name"] for route in llm["vendor"]["routes"]] == [
        "first",
        "second",
    ]


def test_unrelated_save_preserves_newer_on_disk_legacy_change(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    stale = load_config(path)
    newer = LEGACY_WITH_UNKNOWN_PROVIDER_KEY.replace(
        'model = "deepseek-v4-flash"',
        'model = "newer-process-model"',
    )
    path.write_text(newer, encoding="utf-8")
    stale.saved_sync.auto_sync_enabled = True

    save_config(stale, path)

    with path.open("rb") as handle:
        rendered = tomllib.load(handle)
    assert rendered["llm"]["deepseek"]["model"] == "newer-process-model"
    assert rendered["saved_sync"]["auto_sync_enabled"] is True


def test_unrelated_save_preserves_raw_native_section_changed_after_load(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(NATIVE_MODELS_TOML, encoding="utf-8")
    stale = load_config(path)
    model_text = NATIVE_MODELS_TOML.replace(
        "schema_version = 1",
        'schema_version = 1\nvendor_extension = "newer-writer"',
    ).replace(
        'reasoning_effort = "max"',
        'reasoning_effort = "max"\nsecret_tags = ["one", "two"]',
    )
    # A newer writer changed the native section after this process loaded it.
    path.write_text(model_text, encoding="utf-8")
    with path.open("rb") as handle:
        expected = tomllib.load(handle)["models"]
    stale.saved_sync.auto_sync_enabled = True

    save_config(stale, path)

    with path.open("rb") as handle:
        rendered = tomllib.load(handle)
    assert rendered["models"] == expected
    assert "llm" not in rendered


def test_unrelated_save_preserves_both_sections_when_native_and_legacy_exist(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        NATIVE_MODELS_TOML + "\n\n" + LEGACY_WITH_UNKNOWN_PROVIDER_KEY,
        encoding="utf-8",
    )
    before = tomllib.loads(path.read_text(encoding="utf-8"))
    loaded = load_config(path)

    save_config(loaded, path)

    after = tomllib.loads(path.read_text(encoding="utf-8"))
    assert loaded.models.chat.connections[0].id == "deepseek-main"
    assert after["models"] == before["models"]
    assert after["llm"] == before["llm"]


def test_authoritative_save_replaces_both_sections_with_config_models(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        NATIVE_MODELS_TOML + "\n\n" + LEGACY_WITH_UNKNOWN_PROVIDER_KEY,
        encoding="utf-8",
    )
    loaded = load_config(path)
    updated = replace(loaded.models, chat=replace(loaded.models.chat, concurrency=9))
    loaded.models = updated

    save_config(loaded, path, models_authoritative=True)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["models"]["chat"]["concurrency"] == 9
    assert "llm" not in raw


def test_unrelated_default_path_save_does_not_bake_local_models_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    base = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    base.write_text(NATIVE_MODELS_TOML, encoding="utf-8")
    local.write_text("[models.chat]\nconcurrency = 11\n", encoding="utf-8")
    before = tomllib.loads(base.read_text(encoding="utf-8"))["models"]
    loaded = load_config()

    assert loaded.models.chat.concurrency == 11
    assert loaded.model_meta.override_paths == ("models.chat.concurrency",)
    loaded.saved_sync.auto_sync_enabled = True
    save_config(loaded)

    after = tomllib.loads(base.read_text(encoding="utf-8"))["models"]
    assert after == before
    assert after["chat"]["concurrency"] == 5
    assert tomllib.loads(local.read_text(encoding="utf-8"))["models"]["chat"]["concurrency"] == 11


def test_unrelated_default_path_save_does_not_bake_local_legacy_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    base = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    base.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    local.write_text('[llm.deepseek]\nmodel = "local-only-model"\n', encoding="utf-8")
    loaded = load_config()

    assert loaded.models.chat.connections[0].model == "local-only-model"
    assert loaded.model_meta.override_paths == ("llm.deepseek.model",)
    loaded.saved_sync.auto_sync_enabled = True
    save_config(loaded)

    with base.open("rb") as handle:
        llm = tomllib.load(handle)["llm"]
    assert llm["deepseek"]["model"] == "deepseek-v4-flash"
    assert llm["deepseek"]["vendor_option"] == "keep-provider-option"


def test_env_legacy_override_records_the_exact_leaf_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_LLM_CONCURRENCY", "9")
    (tmp_path / "config.toml").write_text(
        LEGACY_WITH_UNKNOWN_PROVIDER_KEY,
        encoding="utf-8",
    )

    loaded = load_config()

    assert loaded.models.chat.concurrency == 9
    assert "llm.concurrency" in loaded.model_meta.override_paths


def test_env_legacy_override_does_not_change_raw_legacy_table_on_unrelated_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_LLM_CONCURRENCY", "9")
    base = tmp_path / "config.toml"
    base.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    loaded = load_config()
    loaded.language = "en"

    save_config(loaded)

    with base.open("rb") as handle:
        llm = tomllib.load(handle)["llm"]
    assert llm["concurrency"] == 4
    assert llm["timeout"] == 300
    assert llm["vendor_extension"] == "keep-me"


def test_local_legacy_override_does_not_bake_into_raw_table_on_unrelated_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    base = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    base.write_text(LEGACY_WITH_UNKNOWN_PROVIDER_KEY, encoding="utf-8")
    local.write_text('[llm.deepseek]\nmodel = "local-only-model"\n', encoding="utf-8")
    loaded = load_config()
    loaded.language = "en"

    save_config(loaded)

    with base.open("rb") as handle:
        llm = tomllib.load(handle)["llm"]
    assert llm["deepseek"]["model"] == "deepseek-v4-flash"
    assert "openai" not in llm
    assert llm["deepseek"]["vendor_option"] == "keep-provider-option"
    assert (
        tomllib.loads(local.read_text(encoding="utf-8"))["llm"]["deepseek"]["model"]
        == "local-only-model"
    )
