from __future__ import annotations

import importlib.util
import json
import os
import sys
import tomllib
import types
from pathlib import Path

import pytest


def _load_bootstrap_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "scripts" / "agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_agent_bootstrap", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def _write_native_config(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        """[models]
schema_version = 1

[models.chat]
concurrency = 4
timeout_seconds = 300

[[models.chat.connections]]
id = "existing-main"
name = "Existing main"
type = "openai_compatible"
preset = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
api_mode = "chat_completions"

[[models.chat.connections]]
id = "kept-fallback"
name = "Kept fallback"
type = "ollama"
model = "qwen2.5:7b"
base_url = "http://127.0.0.1:11434/v1"

[models.embedding]
enabled = false

[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false

[bilibili]
cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("legacy", "connection_type", "preset"),
    [
        ("deepseek", "openai_compatible", "deepseek"),
        ("openai", "openai_compatible", "openai"),
        ("openrouter", "openai_compatible", "openrouter"),
        ("openai_compatible", "openai_compatible", "custom"),
        ("claude", "anthropic_compatible", "anthropic"),
        ("gemini", "gemini_api", ""),
        ("ollama", "ollama", ""),
    ],
)
def test_legacy_provider_alias_maps_to_connection_type_and_preset(
    legacy: str,
    connection_type: str,
    preset: str,
) -> None:
    assert bootstrap.resolve_connection_selection(provider=legacy) == (
        connection_type,
        preset,
    )


def test_parser_uses_connection_type_and_preset_as_canonical_model_flags(
    tmp_path: Path,
) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--connection-type",
            "openai_compatible",
            "--preset",
            "deepseek",
            "--embedding-endpoint",
            "ollama=http://embed-a:11434/v1",
            "--embedding-endpoint",
            "ollama=http://embed-b:11434/v1",
        ]
    )

    assert args.connection_type == "openai_compatible"
    assert args.preset == "deepseek"
    assert args.embedding_endpoint == [
        "ollama=http://embed-a:11434/v1",
        "ollama=http://embed-b:11434/v1",
    ]
    assert not hasattr(args, "module_override")

    with pytest.raises(SystemExit):
        bootstrap.build_arg_parser().parse_args(["--module-override", "soul=ollama:qwen"])


def test_deprecated_provider_alias_surface_is_exact_and_rejects_codex() -> None:
    assert bootstrap.SUPPORTED_PROVIDERS == (
        "openai",
        "claude",
        "gemini",
        "deepseek",
        "ollama",
        "openrouter",
        "openai_compatible",
    )
    assert "codex" not in bootstrap.LEGACY_PROVIDER_CONNECTIONS

    with pytest.raises(SystemExit):
        bootstrap.build_arg_parser().parse_args(["--provider", "codex"])


def test_native_chat_writer_edits_primary_without_dropping_fallback(tmp_path: Path) -> None:
    _write_native_config(tmp_path)

    result = bootstrap.apply_chat_route_config(
        tmp_path,
        connection_type="openai_compatible",
        preset="openrouter",
        model="openai/gpt-5-nano",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-router-key",
        credential_ref=None,
    )

    raw = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    connections = raw["models"]["chat"]["connections"]
    assert result["connection_id"] == "existing-main"
    assert [item["id"] for item in connections] == ["existing-main", "kept-fallback"]
    assert connections[0]["type"] == "openai_compatible"
    assert connections[0]["preset"] == "openrouter"
    assert connections[0]["api_key"] == "test-router-key"
    assert connections[1]["type"] == "ollama"
    assert "[llm" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_native_chat_writer_allocates_id_without_embedding_collision(tmp_path: Path) -> None:
    tmp_path.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[models.embedding]
enabled = true
[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false
[[models.embedding.providers]]
id = "chat-main"
name = "Existing embedding"
type = "ollama"
base_url = "http://127.0.0.1:11434/v1"
""",
        encoding="utf-8",
    )

    result = bootstrap.apply_chat_route_config(
        tmp_path,
        connection_type="openai_compatible",
        preset="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key=None,
        credential_ref=None,
    )

    raw = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert result["connection_id"] == "chat-main-2"
    assert raw["models"]["chat"]["connections"][0]["id"] == "chat-main-2"
    assert raw["models"]["embedding"]["providers"][0]["id"] == "chat-main"


def test_embedding_endpoints_create_ordered_providers_with_shared_model(tmp_path: Path) -> None:
    _write_native_config(tmp_path)

    result = bootstrap.apply_embedding_config(
        tmp_path,
        provider=None,
        model="bge-m3",
        base_url=None,
        api_key=None,
        endpoints=[
            "ollama=http://embed-a:11434/v1",
            "ollama=http://embed-b:11434/v1",
        ],
    )

    raw = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    embedding = raw["models"]["embedding"]
    assert result["provider_ids"] == ["embedding-1", "embedding-2"]
    assert embedding["enabled"] is True
    assert embedding["settings"]["model"] == "bge-m3"
    assert [provider["id"] for provider in embedding["providers"]] == [
        "embedding-1",
        "embedding-2",
    ]
    assert [provider["base_url"] for provider in embedding["providers"]] == [
        "http://embed-a:11434/v1",
        "http://embed-b:11434/v1",
    ]
    assert all("model" not in provider for provider in embedding["providers"])


def test_embedding_endpoint_edits_reuse_positional_ids_and_only_compatible_credentials(
    tmp_path: Path,
) -> None:
    _write_native_config(tmp_path)
    bootstrap.apply_embedding_config(
        tmp_path,
        provider=None,
        model="shared-vector-model",
        base_url=None,
        api_key="test-embedding-key",
        endpoints=[
            "openai_compatible:openai=https://embed-a.example/v1",
            "ollama=http://embed-b:11434/v1",
        ],
    )

    bootstrap.apply_embedding_config(
        tmp_path,
        provider=None,
        model="shared-vector-model",
        base_url=None,
        api_key=None,
        endpoints=[
            "openai_compatible:openai=https://embed-a-edited.example/v1",
            "ollama=http://embed-b-edited:11434/v1",
        ],
    )
    edited = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    providers = edited["models"]["embedding"]["providers"]
    assert [item["id"] for item in providers] == ["embedding-1", "embedding-2"]
    assert providers[0]["api_key"] == "test-embedding-key"

    bootstrap.apply_embedding_config(
        tmp_path,
        provider=None,
        model="shared-vector-model",
        base_url=None,
        api_key=None,
        endpoints=[
            "ollama=http://embed-b-edited:11434/v1",
            "openai_compatible:openai=https://embed-a-edited.example/v1",
        ],
    )
    reordered = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    providers = reordered["models"]["embedding"]["providers"]
    assert [item["id"] for item in providers] == ["embedding-1", "embedding-2"]
    assert [item["type"] for item in providers] == ["ollama", "openai_compatible"]
    assert "api_key" not in providers[0]
    assert "api_key" not in providers[1]


def test_embedding_endpoint_allocates_id_without_chat_collision(tmp_path: Path) -> None:
    _write_native_config(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'id = "existing-main"', 'id = "embedding-1"', 1
        ),
        encoding="utf-8",
    )

    result = bootstrap.apply_embedding_config(
        tmp_path,
        provider=None,
        model="bge-m3",
        base_url=None,
        api_key=None,
        endpoints=["ollama=http://embed-a:11434/v1"],
    )

    assert result["provider_ids"] == ["embedding-2"]


def test_single_embedding_provider_allocates_id_without_chat_collision(tmp_path: Path) -> None:
    _write_native_config(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'id = "existing-main"', 'id = "embedding-main"', 1
        ),
        encoding="utf-8",
    )

    result = bootstrap.apply_embedding_config(
        tmp_path,
        provider="ollama",
        model="bge-m3",
        base_url="http://embed-main:11434/v1",
        api_key=None,
        endpoints=None,
    )

    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert result["provider_ids"] == ["embedding-main-2"]
    assert raw["models"]["embedding"]["providers"][0]["id"] == "embedding-main-2"
    assert raw["models"]["chat"]["connections"][0]["id"] == "embedding-main"


def test_single_embedding_provider_edits_primary_without_dropping_fallback(
    tmp_path: Path,
) -> None:
    _write_native_config(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace("[models.embedding]\nenabled = false", "[models.embedding]\nenabled = true")
        .replace(
            "\n[bilibili]",
            """

[[models.embedding.providers]]
id = "embedding-primary"
name = "Primary Ollama"
type = "ollama"
base_url = "http://old-primary:11434/v1"

[[models.embedding.providers]]
id = "embedding-fallback"
name = "Kept remote fallback"
type = "openai_compatible"
preset = "openai"
base_url = "https://fallback.example/v1"
api_key_env = "KEPT_EMBEDDING_KEY"

[bilibili]""",
        ),
        encoding="utf-8",
    )
    before = tomllib.loads(config_path.read_text(encoding="utf-8"))["models"]["embedding"][
        "providers"
    ][1]

    result = bootstrap.apply_embedding_config(
        tmp_path,
        provider="ollama",
        model="bge-m3",
        base_url="http://new-primary:11434/v1",
        api_key=None,
        endpoints=None,
    )

    providers = tomllib.loads(config_path.read_text(encoding="utf-8"))["models"]["embedding"][
        "providers"
    ]
    assert result["provider_ids"] == ["embedding-primary", "embedding-fallback"]
    assert providers[0]["base_url"] == "http://new-primary:11434/v1"
    assert providers[1] == before


@pytest.mark.parametrize(
    ("alias", "preset", "model"),
    [
        ("openai", "openai", "gpt-5-nano"),
        ("openrouter", "openrouter", "openai/gpt-5-nano"),
    ],
)
def test_provider_alias_run_supplies_required_default_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    preset: str,
    model: str,
) -> None:
    _write_native_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--provider",
            alias,
            "--embedding-provider",
            "",
            "--skip-install",
            "--skip-start",
            "--skip-init",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )

    assert bootstrap.run(args) == 0
    primary = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["models"][
        "chat"
    ]["connections"][0]
    assert primary["preset"] == preset
    assert primary["model"] == model


def test_run_creates_default_primary_when_native_chat_route_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text(
        """[models]
schema_version = 1

[models.chat]
concurrency = 4
timeout_seconds = 300

[models.embedding]
enabled = false

[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false

[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--embedding-provider",
            "",
            "--skip-install",
            "--skip-start",
            "--skip-init",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )

    assert bootstrap.run(args) == 0
    primary = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["models"][
        "chat"
    ]["connections"][0]
    assert primary["id"] == "chat-main"
    assert primary["type"] == "openai_compatible"
    assert primary["preset"] == "deepseek"


def test_native_chat_route_is_untouched_without_explicit_model_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_native_config(tmp_path)
    before = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["models"]["chat"]
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "docker",
            "--skip-start",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )

    assert bootstrap.run(args) == 0

    after = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["models"]["chat"]
    assert after == before


def test_codex_oauth_writer_serializes_only_imported_credential_reference(tmp_path: Path) -> None:
    _write_native_config(tmp_path)

    bootstrap.apply_chat_route_config(
        tmp_path,
        connection_type="codex_oauth",
        preset="",
        model="gpt-5-nano",
        base_url=None,
        api_key=None,
        credential_ref="codex",
    )

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    connection = tomllib.loads(text)["models"]["chat"]["connections"][0]
    assert connection["type"] == "codex_oauth"
    assert connection["credential_ref"] == "codex"
    assert "api_key" not in connection
    assert "oauth_token" not in text


def test_bootstrap_extends_no_proxy_for_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.delenv("no_proxy", raising=False)

    bootstrap.ensure_local_no_proxy()

    assert os.environ["NO_PROXY"] == "example.com,localhost,127.0.0.1,::1"
    assert os.environ["no_proxy"] == "example.com,localhost,127.0.0.1,::1"


def test_bootstrap_defaults_to_lan_accessible_bind_host(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    assert args.host == "0.0.0.0"


def test_bootstrap_connects_to_loopback_when_binding_all_interfaces() -> None:
    assert bootstrap._connect_host_for_bind_host("0.0.0.0") == "127.0.0.1"
    assert bootstrap._connect_host_for_bind_host("::") == "127.0.0.1"
    assert bootstrap._connect_host_for_bind_host("127.0.0.1") == "127.0.0.1"
    assert bootstrap._connect_host_for_bind_host("192.168.1.100") == "192.168.1.100"


def test_is_user_data_only_root_accepts_packaged_data_root(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (tmp_path / "config.local.toml").write_text("[api]\nport = 18420\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()

    assert bootstrap._is_user_data_only_root(tmp_path)


def test_is_user_data_only_root_rejects_unknown_entries(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (tmp_path / "random.txt").write_text("not ours\n", encoding="utf-8")

    assert not bootstrap._is_user_data_only_root(tmp_path)


def test_ensure_repo_checkout_clones_into_existing_user_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "OpenBiliClaw"
    project_dir.mkdir()
    (project_dir / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (project_dir / "data").mkdir()
    (project_dir / "data" / "openbiliclaw.db").write_bytes(b"existing db")

    def fake_run_streaming(cmd: list[str], **_kwargs: object) -> None:
        clone_dir = Path(cmd[-1])
        clone_dir.mkdir(parents=True, exist_ok=True)
        (clone_dir / ".git").mkdir()
        (clone_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (clone_dir / "config.example.toml").write_text("[general]\n", encoding="utf-8")
        (clone_dir / "src").mkdir()
        (clone_dir / "src" / "marker.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "which", lambda name: "git" if name == "git" else None)
    monkeypatch.setattr(bootstrap, "run_streaming", fake_run_streaming)

    result = bootstrap.ensure_repo_checkout(project_dir, "https://example/repo.git", "main")

    assert result == project_dir.resolve()
    assert (project_dir / ".git").exists()
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / "config.example.toml").exists()
    assert (project_dir / "config.toml").read_text(encoding="utf-8") == "language = 'zh'\n"
    assert (project_dir / "data" / "openbiliclaw.db").read_bytes() == b"existing db"


def _write_minimal_config(
    tmp_path: Path,
    *,
    embedding_provider: str = "",
    embedding_model: str = "",
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openai"',
                "",
                "[llm.openai]",
                'api_key = "sk-test"',
                "",
                "[llm.embedding]",
                f'provider = "{embedding_provider}"',
                f'model = "{embedding_model}"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_init_decisions_required_when_source_and_embedding_were_not_explicit(
    tmp_path: Path,
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["embedding", "xhs", "douyin", "youtube"]
    assert decisions["xhs"]["policy"] == "pending"
    assert decisions["douyin"]["policy"] == "pending"
    assert decisions["youtube"]["policy"] == "pending"
    assert decisions["embedding"]["source"] == "missing"


def test_init_decisions_accept_explicit_source_and_embedding_choices(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--embedding-provider",
            "ollama",
            "--embedding-model",
            "bge-m3",
            "--no-xhs",
            "--yes-douyin",
            "--no-youtube",
        ]
    )

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=True)

    assert decisions["missing"] == []
    assert decisions["xhs"]["policy"] == "disabled"
    assert decisions["douyin"]["policy"] == "enabled"
    assert decisions["youtube"]["policy"] == "disabled"
    assert decisions["embedding"]["source"] == "flags"


def test_init_decisions_accept_existing_embedding_but_still_require_sources(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs", "douyin", "youtube"]
    assert decisions["embedding"]["source"] == "config"


def test_init_decisions_required_for_all_optional_sources(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs", "douyin", "youtube"]


def test_apply_embedding_config_writes_native_provider_credentials(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path)

    result = bootstrap.apply_embedding_config(
        tmp_path,
        provider="openai",
        model="text-embedding-3-small",
        base_url="https://embed.example.com/v1",
        api_key="test-embedding-key",
    )

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    embedding = raw["models"]["embedding"]
    assert result["written"] == ["models.embedding"]
    assert embedding["settings"]["model"] == "text-embedding-3-small"
    assert embedding["providers"][0]["type"] == "openai_compatible"
    assert embedding["providers"][0]["preset"] == "openai"
    assert embedding["providers"][0]["base_url"] == "https://embed.example.com/v1"
    assert embedding["providers"][0]["api_key"] == "test-embedding-key"
    assert "[llm" not in text


def test_docker_run_rewrites_default_ollama_embedding_to_compose_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_minimal_config(
        tmp_path,
        embedding_provider="",
        embedding_model="",
    )
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "docker",
            "--skip-start",
            "--embedding-provider",
            "ollama",
            "--embedding-model",
            "bge-m3",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )

    returncode = bootstrap.run(args)

    config = bootstrap.read_simple_toml(tmp_path / "config.toml")
    assert returncode == 0
    embedding = config["models"]["embedding"]
    assert embedding["enabled"] is True
    assert embedding["settings"]["model"] == "bge-m3"
    assert embedding["providers"][0]["type"] == "ollama"
    assert embedding["providers"][0]["base_url"] == "http://ollama:11434/v1"
    assert "llm" not in config


def test_should_auto_wire_embedding_when_unconfigured_local() -> None:
    # Flag-driven install that never passed --embedding-* and left embedding
    # empty → default to local Ollama so dedup isn't silently disabled.
    assert bootstrap.should_auto_wire_embedding(
        embedding_provider_arg=None, effective_provider="", mode="local"
    )


def test_should_not_auto_wire_embedding_when_already_configured() -> None:
    assert not bootstrap.should_auto_wire_embedding(
        embedding_provider_arg=None, effective_provider="gemini", mode="local"
    )


def test_should_not_auto_wire_embedding_when_explicitly_disabled() -> None:
    # User passed --embedding-provider "" to deliberately turn embedding off.
    assert not bootstrap.should_auto_wire_embedding(
        embedding_provider_arg="", effective_provider="", mode="local"
    )


def test_should_not_auto_wire_embedding_under_docker() -> None:
    # The container can't reach the host's Ollama at localhost, so wiring it
    # would just mint a broken config.
    assert not bootstrap.should_auto_wire_embedding(
        embedding_provider_arg=None, effective_provider="", mode="docker"
    )


def test_detect_missing_secrets_defaults_to_deepseek_when_provider_absent(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                "",
                "[llm.deepseek]",
                'api_key = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )

    status = bootstrap.detect_missing_secrets(tmp_path)

    assert status["provider"] == "deepseek"
    assert status["missing"] == ["llm.deepseek.api_key", "bilibili.cookie"]


def test_parser_accepts_openai_compatible_provider(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        ["--project-dir", str(tmp_path), "--provider", "openai_compatible"]
    )

    assert args.provider == "openai_compatible"


def test_detect_missing_secrets_flags_openai_compatible_connection_fields(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openai_compatible"',
                "",
                "[llm.openai_compatible]",
                'api_key = ""',
                'base_url = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )

    status = bootstrap.detect_missing_secrets(tmp_path)

    assert status["provider"] == "openai_compatible"
    assert status["missing"] == [
        "llm.openai_compatible.api_key",
        "llm.openai_compatible.base_url",
        "bilibili.cookie",
    ]


def test_reuse_config_secrets_overlays_compatible_credential_without_replacing_route(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    _write_native_config(target)
    (source / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "deepseek"',
                "",
                "[llm.deepseek]",
                'api_key = "test-reused-key"',
                'model = "source-model-must-not-replace-target"',
                'base_url = "https://source.example/v1"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)
    target_config = bootstrap.read_simple_toml(target / "config.toml")
    connections = target_config["models"]["chat"]["connections"]

    assert "models.chat.connections.existing-main.credential" in summary["reused"]
    assert [item["id"] for item in connections] == ["existing-main", "kept-fallback"]
    assert connections[0]["model"] == "deepseek-v4-flash"
    assert connections[0]["base_url"] == "https://api.deepseek.com"
    assert connections[0]["api_key"] == "test-reused-key"
    assert connections[1]["type"] == "ollama"
    assert "llm" not in target_config


def test_reuse_config_secrets_reserves_exact_id_match_before_compatible_fallback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    target.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "compatible-first"
name = "Compatible first"
type = "openai_compatible"
preset = "deepseek"
model = "target-first-model"
base_url = "https://target-first.example/v1"
api_key_env = "TARGET_FIRST_KEY"
[[models.chat.connections]]
id = "exact-second"
name = "Exact second"
type = "openai_compatible"
preset = "deepseek"
model = "target-second-model"
base_url = "https://target-second.example/v1"
api_key_env = "TARGET_SECOND_KEY"
[models.embedding]
enabled = false
[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false
[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )
    source.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "exact-second"
name = "Source exact"
type = "openai_compatible"
preset = "deepseek"
model = "source-model-must-not-replace-target"
base_url = "https://source.example/v1"
api_key = "test-exact-source-key"
[models.embedding]
enabled = false
[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false
[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)

    connections = tomllib.loads((target / "config.toml").read_text(encoding="utf-8"))["models"][
        "chat"
    ]["connections"]
    assert connections[0]["api_key_env"] == "TARGET_FIRST_KEY"
    assert "api_key" not in connections[0]
    assert connections[1]["api_key"] == "test-exact-source-key"
    assert connections[0]["model"] == "target-first-model"
    assert connections[1]["model"] == "target-second-model"
    assert summary["reused"] == ["models.chat.connections.exact-second.credential"]


def test_reuse_config_secrets_uses_one_legacy_source_credential_only_once(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    target.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "target-chat"
name = "Target Chat"
type = "openai_compatible"
preset = "openai"
model = "gpt-5-nano"
base_url = "https://api.openai.com/v1"
api_key_env = "TARGET_CHAT_KEY"
[models.embedding]
enabled = true
[models.embedding.settings]
model = "text-embedding-3-small"
output_dimensionality = 1536
similarity_threshold = 0.82
multimodal_enabled = false
[[models.embedding.providers]]
id = "target-embedding"
name = "Target Embedding"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "TARGET_EMBEDDING_KEY"
[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )
    source.joinpath("config.toml").write_text(
        """[llm]
default_provider = "openai"
fallback_provider = ""

[llm.openai]
api_key = "test-one-legacy-source"
model = "gpt-5-nano"
base_url = "https://api.openai.com/v1"

[llm.embedding]
provider = "openai"
model = "text-embedding-3-small"
base_url = ""
fallback_enabled = true

[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)

    models = tomllib.loads(target.joinpath("config.toml").read_text(encoding="utf-8"))["models"]
    assert models["chat"]["connections"][0]["api_key"] == "test-one-legacy-source"
    assert models["embedding"]["providers"][0]["api_key_env"] == "TARGET_EMBEDDING_KEY"
    assert "api_key" not in models["embedding"]["providers"][0]
    assert summary["reused"] == ["models.chat.connections.target-chat.credential"]


def test_reuse_config_secrets_keeps_distinct_native_source_records_independent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    target.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "target-chat"
name = "Target Chat"
type = "openai_compatible"
preset = "openai"
model = "gpt-5-nano"
base_url = "https://api.openai.com/v1"
api_key_env = "TARGET_CHAT_KEY"
[models.embedding]
enabled = true
[models.embedding.settings]
model = "text-embedding-3-small"
output_dimensionality = 1536
similarity_threshold = 0.82
multimodal_enabled = false
[[models.embedding.providers]]
id = "target-embedding"
name = "Target Embedding"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "TARGET_EMBEDDING_KEY"
[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )
    source.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "source-chat"
name = "Source Chat"
type = "openai_compatible"
preset = "openai"
model = "gpt-5-nano"
base_url = "https://api.openai.com/v1"
api_key = "test-same-value-distinct-records"
[models.embedding]
enabled = true
[models.embedding.settings]
model = "text-embedding-3-small"
output_dimensionality = 1536
similarity_threshold = 0.82
multimodal_enabled = false
[[models.embedding.providers]]
id = "source-embedding"
name = "Source Embedding"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
api_key = "test-same-value-distinct-records"
[bilibili]
cookie = ""
""",
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)

    models = tomllib.loads(target.joinpath("config.toml").read_text(encoding="utf-8"))["models"]
    assert models["chat"]["connections"][0]["api_key"] == ("test-same-value-distinct-records")
    assert models["embedding"]["providers"][0]["api_key"] == ("test-same-value-distinct-records")
    assert summary["reused"] == [
        "models.chat.connections.target-chat.credential",
        "models.embedding.providers.target-embedding.credential",
    ]


@pytest.mark.parametrize(
    ("explicit_key", "expected_key"),
    [
        (None, "test-reused-openai-key"),
        ("test-explicit-openai-key", "test-explicit-openai-key"),
    ],
)
def test_run_selects_native_route_before_reuse_and_keeps_explicit_secret_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explicit_key: str | None,
    expected_key: str,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    target.joinpath("config.toml").write_bytes(Path("config.example.toml").read_bytes())
    source.joinpath("config.toml").write_text(
        """[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "source-openai"
name = "Source OpenAI"
type = "openai_compatible"
preset = "openai"
model = "gpt-5-nano"
base_url = "https://api.openai.com/v1"
api_key = "test-reused-openai-key"
[models.embedding]
enabled = false
[models.embedding.settings]
model = "bge-m3"
output_dimensionality = 1024
similarity_threshold = 0.82
multimodal_enabled = false
[bilibili]
cookie = "test-reused-cookie"
""",
        encoding="utf-8",
    )
    source.joinpath("data").mkdir()
    source.joinpath("data", "bilibili_cookie.json").write_text(
        json.dumps({"cookie": "test-reused-cookie-file"}),
        encoding="utf-8",
    )
    argv = [
        "--project-dir",
        str(target),
        "--mode",
        "local",
        "--reuse-from",
        str(source),
        "--connection-type",
        "openai_compatible",
        "--preset",
        "openai",
        "--embedding-provider",
        "",
        "--bilibili-cookie",
        "test-explicit-cookie",
        "--skip-install",
        "--skip-start",
        "--skip-init",
    ]
    if explicit_key is not None:
        argv.extend(["--llm-api-key", explicit_key])
    args = bootstrap.build_arg_parser().parse_args(argv)
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: target / "config.toml",
    )

    assert bootstrap.run(args) == 0

    raw = tomllib.loads(target.joinpath("config.toml").read_text(encoding="utf-8"))
    primary = raw["models"]["chat"]["connections"][0]
    cookie_file = json.loads(
        target.joinpath("data", "bilibili_cookie.json").read_text(encoding="utf-8")
    )
    output = capsys.readouterr().out
    assert primary["type"] == "openai_compatible"
    assert primary["preset"] == "openai"
    assert primary["api_key"] == expected_key
    assert raw["bilibili"]["cookie"] == "test-explicit-cookie"
    assert cookie_file == {"cookie": "test-explicit-cookie"}
    assert "test-reused-openai-key" not in output
    assert "test-explicit-openai-key" not in output
    assert "test-reused-cookie" not in output
    assert "test-explicit-cookie" not in output


def _write_ordered_embedding_route(
    project_dir: Path,
    *,
    chat_credential: str = 'api_key_env = "DEEPSEEK_API_KEY"',
    openai_credential: str = 'api_key_env = "TARGET_OPENAI_EMBEDDING_KEY"',
    custom_credential: str = 'api_key_env = "TARGET_CUSTOM_EMBEDDING_KEY"',
    include_remote: bool = True,
    bilibili_cookie: str = "",
) -> None:
    remote_providers = (
        f"""[[models.embedding.providers]]
id = "openai-second"
name = "OpenAI second"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
{openai_credential}
[[models.embedding.providers]]
id = "custom-third"
name = "Custom third"
type = "openai_compatible"
preset = "custom"
base_url = "https://embedding.example.test/v1"
{custom_credential}
"""
        if include_remote
        else ""
    )
    project_dir.joinpath("config.toml").write_text(
        f"""[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "chat-main"
name = "Chat main"
type = "openai_compatible"
preset = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
{chat_credential}
api_mode = "chat_completions"
[models.embedding]
enabled = true
[models.embedding.settings]
model = "text-embedding-3-small"
output_dimensionality = 1536
similarity_threshold = 0.82
multimodal_enabled = false
[[models.embedding.providers]]
id = "local-first"
name = "Local first"
type = "ollama"
base_url = "http://127.0.0.1:11434/v1"
{remote_providers}
[bilibili]
cookie = "{bilibili_cookie}"
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize("reuse_credentials", [False, True])
def test_run_embedding_key_only_updates_every_credential_capable_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reuse_credentials: bool,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_ordered_embedding_route(target)
    argv = [
        "--project-dir",
        str(target),
        "--mode",
        "local",
        "--embedding-api-key",
        "test-new-embedding-key",
        "--skip-install",
        "--skip-start",
        "--skip-init",
        "--skip-ollama-setup",
    ]
    if reuse_credentials:
        source = tmp_path / "source"
        source.mkdir()
        _write_ordered_embedding_route(
            source,
            openai_credential='api_key = "test-reused-openai-key"',
            custom_credential='api_key = "test-reused-custom-key"',
        )
        argv.extend(["--reuse-from", str(source)])
    args = bootstrap.build_arg_parser().parse_args(argv)
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: target / "config.toml",
    )
    before = tomllib.loads(target.joinpath("config.toml").read_text(encoding="utf-8"))["models"][
        "embedding"
    ]

    assert bootstrap.run(args) == 0

    after = tomllib.loads(target.joinpath("config.toml").read_text(encoding="utf-8"))["models"][
        "embedding"
    ]
    before_route = [
        (item["id"], item["name"], item["type"], item.get("preset"), item["base_url"])
        for item in before["providers"]
    ]
    after_route = [
        (item["id"], item["name"], item["type"], item.get("preset"), item["base_url"])
        for item in after["providers"]
    ]
    output = capsys.readouterr().out
    assert after["enabled"] is True
    assert after["settings"] == before["settings"]
    assert after_route == before_route
    assert "api_key" not in after["providers"][0]
    assert "api_key_env" not in after["providers"][0]
    assert [item["api_key"] for item in after["providers"][1:]] == [
        "test-new-embedding-key",
        "test-new-embedding-key",
    ]
    assert all("api_key_env" not in item for item in after["providers"][1:])
    assert "test-new-embedding-key" not in output
    assert "test-reused-openai-key" not in output
    assert "test-reused-custom-key" not in output


def test_run_embedding_key_only_rejects_route_without_credential_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_ordered_embedding_route(tmp_path, include_remote=False)
    config_path = tmp_path / "config.toml"
    before = config_path.read_bytes()
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--embedding-api-key",
            "test-unused-embedding-key",
            "--skip-install",
            "--skip-start",
            "--skip-init",
            "--skip-ollama-setup",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: config_path,
    )

    assert bootstrap.run(args) == 2

    output = capsys.readouterr().out
    assert "requires an existing credential-capable embedding provider" in output
    assert "test-unused-embedding-key" not in output
    assert config_path.read_bytes() == before


def test_run_embedding_key_only_preflight_blocks_all_reuse_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    _write_ordered_embedding_route(target, include_remote=False)
    _write_ordered_embedding_route(
        source,
        chat_credential='api_key = "test-reused-chat-key"',
        openai_credential='api_key = "test-reused-openai-key"',
        custom_credential='api_key = "test-reused-custom-key"',
        bilibili_cookie="test-reused-bilibili-cookie",
    )
    source_cookie = source / "data" / "bilibili_cookie.json"
    source_cookie.parent.mkdir()
    source_cookie.write_text(
        json.dumps({"cookie": "test-reused-cookie-file"}),
        encoding="utf-8",
    )
    config_path = target / "config.toml"
    before = config_path.read_bytes()
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(target),
            "--mode",
            "local",
            "--reuse-from",
            str(source),
            "--embedding-api-key",
            "test-new-embedding-key",
            "--skip-install",
            "--skip-start",
            "--skip-init",
            "--skip-ollama-setup",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: config_path,
    )

    assert bootstrap.run(args) == 2

    output = capsys.readouterr().out
    assert "requires an existing credential-capable embedding provider" in output
    assert "secrets_reused" not in output
    assert {
        "config_unchanged": config_path.read_bytes() == before,
        "target_data_created": (target / "data").exists(),
        "target_cookie_created": (target / "data" / "bilibili_cookie.json").exists(),
    } == {
        "config_unchanged": True,
        "target_data_created": False,
        "target_cookie_created": False,
    }
    assert "test-new-embedding-key" not in output
    assert "test-reused-chat-key" not in output
    assert "test-reused-openai-key" not in output
    assert "test-reused-custom-key" not in output
    assert "test-reused-bilibili-cookie" not in output
    assert "test-reused-cookie-file" not in output


def test_reuse_config_secrets_skips_incompatible_source_route(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    _write_native_config(target)
    (source / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openrouter"',
                "",
                "[llm.openrouter]",
                'api_key = "test-router-key"',
                'model = "anthropic/claude-sonnet-4-6"',
                'base_url = "https://openrouter.ai/api/v1"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)
    target_config = bootstrap.read_simple_toml(target / "config.toml")
    connections = target_config["models"]["chat"]["connections"]

    assert any("openai_compatible:openrouter" in item for item in summary["skipped"])
    assert [item["id"] for item in connections] == ["existing-main", "kept-fallback"]
    assert connections[0]["preset"] == "deepseek"
    assert connections[0]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "llm" not in target_config


def test_reuse_config_secrets_has_no_legacy_target_writer() -> None:
    source = Path("scripts/agent_bootstrap.py").read_text(encoding="utf-8")
    body = source.split("def reuse_config_secrets(", 1)[1].split("\ndef persist_cookie_file", 1)[0]

    assert "default_provider" not in body
    assert 'f"llm.' not in body
    assert '"llm"' not in body


def test_run_reports_auto_wired_embedding_from_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_native_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
        ]
    )

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_ollama_ready",
        lambda _models: {"running": True, "pulled": ["bge-m3"]},
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    final = status_lines[-1]

    assert returncode == 0
    assert final["message"] == "skipped_start"
    embedding = final["details"]["init_decisions"]["embedding"]
    assert embedding["source"] == "config"
    assert embedding["provider"] == "ollama"
    assert embedding["providers"] == ["ollama"]
    assert embedding["model"] == "bge-m3"
    assert embedding["enabled"] is True
    assert embedding["explicit"] is True


def test_build_init_command_appends_all_source_flags_for_local(tmp_path: Path) -> None:
    command = bootstrap.build_init_command(
        "local",
        tmp_path,
        "--no-xhs",
        "--no-douyin",
        "--yes-youtube",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    assert command[-8:] == [
        "init",
        "--no-xhs",
        "--no-douyin",
        "--yes-youtube",
        "--bilibili-favorite-limit",
        "120",
        "--bilibili-follow-limit",
        "80",
    ]


def test_human_install_choice_parser_accepts_numbers_and_aliases() -> None:
    assert [item[0] for item in bootstrap.HUMAN_LLM_MENU] == [
        "openai_compatible",
        "anthropic_compatible",
        "gemini_api",
        "ollama",
        "codex_oauth",
    ]
    assert bootstrap.resolve_human_llm_choice("") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("1") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("2") == "anthropic_compatible"
    assert bootstrap.resolve_human_llm_choice("relay") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("ollama") == "ollama"
    assert bootstrap.resolve_human_llm_choice("bad") is None


def test_human_install_preset_parser_runs_after_connection_type() -> None:
    assert bootstrap.resolve_human_preset_choice("openai_compatible", "") == "deepseek"
    assert bootstrap.resolve_human_preset_choice("openai_compatible", "2") == "openai"
    assert bootstrap.resolve_human_preset_choice("openai_compatible", "relay") == "custom"
    assert bootstrap.resolve_human_preset_choice("anthropic_compatible", "") == "anthropic"
    assert bootstrap.resolve_human_preset_choice("gemini_api", "") == ""
    assert bootstrap.resolve_human_preset_choice("openai_compatible", "bad") is None


def test_secret_presence_label_never_includes_secret_value() -> None:
    assert "sk-test" not in bootstrap.mask_secret_for_prompt("sk-test")
    assert bootstrap.mask_secret_for_prompt("") == "not set"


def test_collect_human_install_wizard_refuses_without_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="interactive confirmation requires a terminal"):
        bootstrap.collect_human_install_wizard()


def test_human_install_answers_reject_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        bootstrap.HumanInstallAnswers(provider="openai-compat")


def test_collect_human_llm_defaults_to_deepseek() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["", "", ""])
    secret_inputs = iter(["test-deepseek-key"])
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.connection_type == "openai_compatible"
    assert answer.preset == "deepseek"
    assert answer.llm_api_key == "test-deepseek-key"
    assert answer.llm_model == "deepseek-v4-flash"
    assert answer.llm_base_url is None
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_llm_openai_compat_relay_collects_triplet() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["1", "4", "", "https://relay.example/v1"])
    secret_inputs = iter(["test-relay-key"])
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.connection_type == "openai_compatible"
    assert answer.preset == "custom"
    assert answer.llm_base_url == "https://relay.example/v1"
    assert answer.llm_api_key == "test-relay-key"
    assert answer.llm_model == "gpt-5-nano"
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_llm_openai_compat_numeric_preset_uses_vendor_defaults() -> None:
    plain_inputs = iter(["1", "3", ""])
    secret_inputs = iter(["test-openrouter-key"])

    answer = bootstrap.collect_human_llm_config(
        input_func=lambda _prompt: next(plain_inputs),
        secret_input_func=lambda _prompt: next(secret_inputs),
    )

    assert answer.connection_type == "openai_compatible"
    assert answer.preset == "openrouter"
    assert answer.llm_base_url is None
    assert answer.llm_api_key == "test-openrouter-key"
    assert answer.llm_model == "openai/gpt-5-nano"


def test_collect_human_llm_ollama_needs_no_api_key() -> None:
    plain_inputs = iter(["4", "qwen2.5:7b"])
    secret_prompts: list[str] = []
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda _prompt: next(plain_inputs),
        secret_input_func=lambda prompt: secret_prompts.append(prompt) or "",
    )

    assert answer.connection_type == "ollama"
    assert answer.preset == ""
    assert answer.llm_api_key == ""
    assert answer.llm_model == "qwen2.5:7b"
    assert secret_prompts == []


def test_collect_human_llm_does_not_reuse_key_across_providers() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["1", "2", ""])
    secret_inputs = iter(["", "test-openai-key"])

    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
        existing_provider="deepseek",
        existing_api_key="sk-old-deepseek",
    )

    assert answer.connection_type == "openai_compatible"
    assert answer.preset == "openai"
    assert answer.llm_api_key == "test-openai-key"
    assert all("press Enter to reuse" not in prompt for _kind, prompt in prompts)


def test_prompt_secret_converts_getpass_warning_to_runtime_error() -> None:
    import getpass

    def raise_getpass_warning(_prompt: str) -> str:
        raise getpass.GetPassWarning("echo cannot be disabled")

    with pytest.raises(RuntimeError, match="cannot disable terminal echo"):
        bootstrap._prompt_secret(raise_getpass_warning, "API Key")


def test_collect_human_install_wizard_default_path() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    secret_inputs = iter(["test-deepseek-key"])

    answer = bootstrap.collect_human_install_wizard(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.connection_type == "openai_compatible"
    assert answer.preset == "deepseek"
    assert answer.embedding_provider == "ollama"
    assert answer.embedding_model == "bge-m3"
    assert answer.bilibili_favorite_limit == 300
    assert answer.bilibili_follow_limit == 100
    assert answer.xhs is False
    assert answer.douyin is False
    assert answer.youtube is False
    assert answer.cookie_mode == "extension"
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_install_wizard_manual_cookie() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(
        [
            "4",
            "qwen2.5:7b",
            "3",
            "120",
            "80",
            "n",
            "y",
            "n",
            "manual",
        ]
    )
    secret_inputs = iter(["SESSDATA=test; bili_jct=test; DedeUserID=1"])

    answer = bootstrap.collect_human_install_wizard(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.connection_type == "ollama"
    assert answer.embedding_provider == ""
    assert answer.embedding_model == ""
    assert answer.douyin is True
    assert answer.cookie_mode == "manual"
    assert answer.bilibili_cookie.startswith("SESSDATA=")
    assert any(kind == "secret" and "Cookie" in prompt for kind, prompt in prompts)


def test_apply_human_install_answers_sets_all_bootstrap_args(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])
    answers = bootstrap.HumanInstallAnswers(
        provider="deepseek",
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
        embedding_provider="ollama",
        embedding_model="bge-m3",
        xhs=False,
        douyin=True,
        youtube=False,
        cookie_mode="manual",
        bilibili_cookie="SESSDATA=test",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    bootstrap.apply_human_install_answers_to_args(args, answers)

    assert args.provider is None
    assert args.connection_type == "openai_compatible"
    assert args.preset == "deepseek"
    assert args.llm_api_key == "test-key"
    assert args.llm_model == "deepseek-v4-flash"
    assert args.embedding_provider == "ollama"
    assert args.embedding_model == "bge-m3"
    assert args.no_xhs is True
    assert args.yes_douyin is True
    assert args.no_youtube is True
    assert args.bilibili_cookie == "SESSDATA=test"
    assert args.bilibili_favorite_limit == 120
    assert args.bilibili_follow_limit == 80


def test_apply_human_install_answers_does_not_clear_existing_secret_on_empty_answer(
    tmp_path: Path,
) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        ["--project-dir", str(tmp_path), "--llm-api-key", "explicit"]
    )
    answers = bootstrap.HumanInstallAnswers(provider="deepseek", llm_api_key="")

    bootstrap.apply_human_install_answers_to_args(args, answers)

    assert args.llm_api_key == "explicit"


def test_apply_human_install_answers_reconciles_extension_wait_flag(
    tmp_path: Path,
) -> None:
    for cookie_mode, expected_wait in [
        ("extension", True),
        ("manual", False),
        ("existing", False),
    ]:
        args = bootstrap.build_arg_parser().parse_args(
            [
                "--project-dir",
                str(tmp_path),
                "--wait-for-extension-cookie",
            ]
        )
        answers = bootstrap.HumanInstallAnswers(
            provider="deepseek",
            cookie_mode=cookie_mode,
            bilibili_cookie="SESSDATA=test" if cookie_mode == "manual" else "",
        )

        bootstrap.apply_human_install_answers_to_args(args, answers)

        assert args.wait_for_extension_cookie is expected_wait


def test_run_interactive_confirm_collects_full_human_install_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        bootstrap,
        "collect_human_install_wizard",
        lambda **_kwargs: bootstrap.HumanInstallAnswers(
            provider="deepseek",
            llm_api_key="test-new-key",
            llm_model="deepseek-v4-flash",
            embedding_provider="ollama",
            embedding_model="bge-m3",
            xhs=False,
            douyin=False,
            youtube=False,
            cookie_mode="manual",
            bilibili_cookie="SESSDATA=test; bili_jct=test; DedeUserID=1",
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_ollama_ready",
        lambda _models: {"running": True, "pulled": ["bge-m3"]},
    )

    returncode = bootstrap.run(args)

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    output = capsys.readouterr().out
    raw = tomllib.loads(text)

    assert returncode == 0
    assert raw["models"]["chat"]["connections"][0]["type"] == "openai_compatible"
    assert raw["models"]["chat"]["connections"][0]["preset"] == "deepseek"
    assert raw["models"]["chat"]["connections"][0]["api_key"] == "test-new-key"
    assert raw["models"]["embedding"]["providers"][0]["type"] == "ollama"
    assert "llm" not in raw
    assert args.wait_for_extension_cookie is False
    assert "test-new-key" not in output
    assert "SESSDATA=test" not in output


def test_run_interactive_confirm_without_tty_returns_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    assert returncode == 2
    assert "interactive confirmation requires a terminal" in output


def test_run_interactive_confirm_getpass_warning_returns_interactive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        bootstrap,
        "collect_human_install_wizard",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cannot disable terminal echo for secret prompt")
        ),
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 2
    assert status_lines[-1]["status"] == "error"
    assert status_lines[-1]["details"]["step"] == "interactive_confirm"
    assert "unexpected" not in status_lines[-1]["message"]


def test_run_interactive_confirm_apply_error_returns_interactive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )
    answers = bootstrap.HumanInstallAnswers(provider="deepseek")

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(bootstrap, "collect_human_install_wizard", lambda **_kwargs: answers)
    monkeypatch.setattr(
        bootstrap,
        "apply_human_install_answers_to_args",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unknown provider")),
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 2
    assert status_lines[-1]["status"] == "error"
    assert status_lines[-1]["details"]["step"] == "interactive_confirm"
    assert "unexpected" not in status_lines[-1]["message"]


def test_interactive_answers_apply_source_flags() -> None:
    answers = bootstrap.InitConfirmationAnswers(
        embedding_provider="ollama",
        embedding_model="bge-m3",
        xhs=False,
        douyin=True,
        youtube=False,
        cookie_mode="manual",
        bilibili_cookie="SESSDATA=test; bili_jct=test; DedeUserID=1",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    argv = bootstrap.confirmation_answers_to_bootstrap_args(answers)

    assert argv == [
        "--embedding-provider",
        "ollama",
        "--embedding-model",
        "bge-m3",
        "--no-xhs",
        "--yes-douyin",
        "--no-youtube",
        "--bilibili-favorite-limit",
        "120",
        "--bilibili-follow-limit",
        "80",
        "--bilibili-cookie",
        "SESSDATA=test; bili_jct=test; DedeUserID=1",
    ]


def test_collect_interactive_confirmations_collects_bilibili_limits() -> None:
    inputs = iter(["", "", "120", "80", "n", "y", "n", "manual", "SESSDATA=test"])

    answers = bootstrap.collect_interactive_confirmations(input_func=lambda _prompt: next(inputs))

    assert answers.embedding_provider == "ollama"
    assert answers.embedding_model == "bge-m3"
    assert answers.bilibili_favorite_limit == 120
    assert answers.bilibili_follow_limit == 80
    assert answers.xhs is False
    assert answers.douyin is True
    assert answers.youtube is False
    assert answers.cookie_mode == "manual"
    assert answers.bilibili_cookie == "SESSDATA=test"


def test_collect_interactive_confirmations_requires_input_func() -> None:
    with pytest.raises(RuntimeError, match="interactive confirmation requires a terminal"):
        bootstrap.collect_interactive_confirmations(input_func=None)


def test_wait_for_cookie_sync_returns_when_cookie_appears(tmp_path: Path) -> None:
    calls = {"count": 0}

    def detector(_project_dir: Path) -> dict[str, object]:
        calls["count"] += 1
        missing = ["bilibili.cookie"] if calls["count"] == 1 else []
        return {"missing": missing}

    assert (
        bootstrap.wait_for_cookie_sync(
            tmp_path,
            timeout_seconds=1,
            interval_seconds=0,
            detector=detector,
        )
        is True
    )


def test_wait_for_cookie_sync_times_out(tmp_path: Path) -> None:
    assert (
        bootstrap.wait_for_cookie_sync(
            tmp_path,
            timeout_seconds=0.01,
            interval_seconds=0,
            detector=lambda _project_dir: {"missing": ["bilibili.cookie"]},
        )
        is False
    )


def _service_check_runner(payload: dict[str, object]):
    def runner(
        _cmd: list[str],
        *,
        check: bool = True,
        cwd: Path | None = None,
    ) -> bootstrap.CommandResult:
        return bootstrap.CommandResult(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    return runner


def test_pre_init_service_checks_pass_when_probe_reports_services_ready(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": True, "provider": "deepseek", "error": ""},
            "embedding": {
                "available": True,
                "provider": "ollama",
                "model": "bge-m3",
                "error": "",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is True
    assert result["failed"] == []
    assert result["services"]["llm"]["provider"] == "deepseek"
    assert result["services"]["embedding"]["provider"] == "ollama"


def test_pre_init_service_checks_fail_when_llm_probe_fails(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": False, "provider": "deepseek", "error": "401 unauthorized"},
            "embedding": {
                "available": True,
                "provider": "ollama",
                "model": "bge-m3",
                "error": "",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is False
    assert result["failed"] == ["llm"]
    assert result["services"]["llm"]["error"] == "401 unauthorized"


def test_pre_init_service_checks_fail_when_embedding_probe_fails(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": True, "provider": "deepseek", "error": ""},
            "embedding": {
                "available": False,
                "provider": "ollama",
                "model": "bge-m3",
                "error": "empty embedding vector",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is False
    assert result["failed"] == ["embedding"]
    assert result["services"]["embedding"]["error"] == "empty embedding vector"


def test_pre_init_service_checks_accept_disabled_embedding(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": True, "provider": "deepseek", "error": ""},
            "embedding": {
                "available": True,
                "provider": "",
                "model": "",
                "skipped": True,
                "error": "",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is True
    assert result["failed"] == []
    assert result["services"]["embedding"]["skipped"] is True


def test_pre_init_service_check_process_failure_uses_fixed_secret_safe_error(
    tmp_path: Path,
) -> None:
    sentinel = "sentinel-secret-from-subprocess-stderr"

    def runner(
        _cmd: list[str],
        *,
        check: bool = True,
        cwd: Path | None = None,
    ) -> bootstrap.CommandResult:
        return bootstrap.CommandResult(returncode=7, stdout="", stderr=sentinel)

    result = bootstrap.run_pre_init_service_checks(tmp_path, "local", runner=runner)

    assert result["available"] is False
    assert result["failed"] == ["llm", "embedding"]
    assert result["services"]["llm"]["error"] == "service_check_process_failed"
    assert result["services"]["embedding"]["error"] == "service_check_process_failed"
    assert sentinel not in json.dumps(result, ensure_ascii=False)


def test_pre_init_probe_uses_native_stable_ids_without_route_fallback() -> None:
    probe = bootstrap.SERVICE_CHECK_PROBE

    assert "cfg.models.chat.connections[0]" in probe
    assert "complete_connection(" in probe
    assert "primary.id" in probe
    assert "ignore_circuit=True" in probe
    assert "cfg.models.embedding" in probe
    assert "for provider in embedding_cfg.providers" in probe
    assert "probe_provider(provider.id)" in probe
    assert "cfg.llm" not in probe
    assert "build_llm_registry" not in probe
    assert "{exc}" not in probe
    assert '"exact_chat_probe_failed"' in probe
    assert '"exact_embedding_probe_failed"' in probe


def test_pre_init_probe_never_emits_exception_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "sentinel-secret-must-not-appear"

    def fail_load() -> object:
        raise RuntimeError(sentinel)

    config_module = types.ModuleType("openbiliclaw.config")
    config_module.load_config = fail_load  # type: ignore[attr-defined]
    factory_module = types.ModuleType("openbiliclaw.llm.connection_factory")
    factory_module.AdapterRuntimeOptions = object  # type: ignore[attr-defined]
    factory_module.build_chat_adapter = object  # type: ignore[attr-defined]
    factory_module.build_embedding_adapter = object  # type: ignore[attr-defined]
    route_module = types.ModuleType("openbiliclaw.llm.route")
    route_module.OrderedLLMRoute = object  # type: ignore[attr-defined]
    route_module.RouteConnection = object  # type: ignore[attr-defined]
    embedding_module = types.ModuleType("openbiliclaw.llm.embedding_route")
    embedding_module.OrderedEmbeddingRoute = object  # type: ignore[attr-defined]
    for name, module in {
        "openbiliclaw.config": config_module,
        "openbiliclaw.llm.connection_factory": factory_module,
        "openbiliclaw.llm.route": route_module,
        "openbiliclaw.llm.embedding_route": embedding_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    exec(bootstrap.SERVICE_CHECK_PROBE, {})

    output = capsys.readouterr().out
    assert sentinel not in output
    payload = json.loads(output)
    assert payload["services"]["llm"]["error"] == "exact_chat_probe_failed"
    assert payload["services"]["embedding"]["error"] == "exact_embedding_probe_failed"


def test_pre_init_probe_attempts_every_embedding_provider_after_secret_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "sentinel-provider-secret-must-not-appear"
    probed: list[str] = []
    primary = types.SimpleNamespace(id="chat-primary", type="ollama", preset="")
    providers = (
        types.SimpleNamespace(id="embedding-first", type="openai_compatible"),
        types.SimpleNamespace(id="embedding-second", type="ollama"),
    )
    settings = types.SimpleNamespace(model="bge-m3")
    config = types.SimpleNamespace(
        models=types.SimpleNamespace(
            chat=types.SimpleNamespace(connections=(primary,)),
            embedding=types.SimpleNamespace(
                enabled=True,
                providers=providers,
                settings=settings,
            ),
        )
    )

    class FakeChatRoute:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def complete_connection(self, *_args: object, **_kwargs: object) -> object:
            return types.SimpleNamespace(content="OK")

    class FakeEmbeddingRoute:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def probe_provider(self, provider_id: str) -> None:
            probed.append(provider_id)
            if provider_id == "embedding-first":
                raise RuntimeError(sentinel)

    config_module = types.ModuleType("openbiliclaw.config")
    config_module.load_config = lambda: config  # type: ignore[attr-defined]
    factory_module = types.ModuleType("openbiliclaw.llm.connection_factory")
    factory_module.AdapterRuntimeOptions = (  # type: ignore[attr-defined]
        lambda **_kwargs: object()
    )
    factory_module.build_chat_adapter = (  # type: ignore[attr-defined]
        lambda *_args, **_kwargs: object()
    )
    factory_module.build_embedding_adapter = (  # type: ignore[attr-defined]
        lambda provider, *_args, **_kwargs: provider.id
    )
    route_module = types.ModuleType("openbiliclaw.llm.route")
    route_module.OrderedLLMRoute = FakeChatRoute  # type: ignore[attr-defined]
    route_module.RouteConnection = (  # type: ignore[attr-defined]
        lambda **_kwargs: object()
    )
    embedding_module = types.ModuleType("openbiliclaw.llm.embedding_route")
    embedding_module.OrderedEmbeddingRoute = FakeEmbeddingRoute  # type: ignore[attr-defined]
    for name, module in {
        "openbiliclaw.config": config_module,
        "openbiliclaw.llm.connection_factory": factory_module,
        "openbiliclaw.llm.route": route_module,
        "openbiliclaw.llm.embedding_route": embedding_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    exec(bootstrap.SERVICE_CHECK_PROBE, {})

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert probed == ["embedding-first", "embedding-second"]
    assert payload["services"]["embedding"]["available"] is False
    assert payload["services"]["embedding"]["error"] == "exact_embedding_probe_failed"
    assert sentinel not in output


def test_run_blocks_auto_init_when_pre_init_service_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
        ]
    )
    init_calls: list[object] = []

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(bootstrap, "start_local_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        bootstrap,
        "run_pre_init_service_checks",
        lambda *_args, **_kwargs: {
            "available": False,
            "failed": ["embedding"],
            "services": {
                "llm": {"available": True, "provider": "openai", "error": ""},
                "embedding": {
                    "available": False,
                    "provider": "ollama",
                    "model": "bge-m3",
                    "error": "empty embedding vector",
                },
            },
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "run_init_streaming",
        lambda *args, **_kwargs: init_calls.append(args) or 0,
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 0
    assert init_calls == []
    assert any(
        event["status"] == "service_check_failed"
        and event["message"] == "pre_init_service_check_failed"
        for event in status_lines
    )


def test_docker_runtime_config_copy_commands(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "config.toml").write_text("[llm]\n", encoding="utf-8")
    (data_dir / "bilibili_cookie.json").write_text('{"cookie":"x"}', encoding="utf-8")

    commands = bootstrap.build_docker_runtime_sync_commands(tmp_path)

    assert [
        "docker",
        "cp",
        str(tmp_path / "config.toml"),
        "openbiliclaw-backend:/app/runtime/config.toml",
    ] in commands
    assert [
        "docker",
        "cp",
        str(data_dir / "bilibili_cookie.json"),
        "openbiliclaw-backend:/app/runtime/data/bilibili_cookie.json",
    ] in commands


def test_docker_secret_detector_command_reads_runtime_config() -> None:
    command = bootstrap.build_docker_missing_secrets_command()
    script = command[-1]

    assert command[:3] == ["docker", "exec", "openbiliclaw-backend"]
    assert "/app/runtime/config.toml" in " ".join(command)
    assert "/app/runtime/data/bilibili_cookie.json" in " ".join(command)
    assert 'data.get("models", {})' in script
    assert 'chat.get("connections", [])' in script
    assert 'primary.get("api_key_env"' in script
    assert "default_provider" not in script


def test_build_init_command_appends_explicit_source_flags_for_docker(tmp_path: Path) -> None:
    command = bootstrap.build_init_command(
        "docker",
        tmp_path,
        "--yes-xhs",
        "--yes-douyin",
        "--no-youtube",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    assert command == [
        "docker",
        "exec",
        "-i",
        "openbiliclaw-backend",
        "openbiliclaw",
        "init",
        "--yes-xhs",
        "--yes-douyin",
        "--no-youtube",
        "--bilibili-favorite-limit",
        "120",
        "--bilibili-follow-limit",
        "80",
    ]


def test_run_init_streaming_emits_machine_readable_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [
        sys.executable,
        "-c",
        "\n".join(
            [
                "print('1/4 拉取数据', flush=True)",
                "print('  · 分析偏好: 已用 20s / 预计还需 ~50s', flush=True)",
                "print('阶段完成: 当前池子 0/15，本轮发现 20 条', flush=True)",
            ]
        ),
    ]

    returncode = bootstrap.run_init_streaming(command, cwd=None, check=True)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    progress_events = [event for event in status_lines if event["message"] == "init_progress"]
    assert returncode == 0
    assert "1/4 拉取数据" in output
    assert any(event["details"]["phase"] == "1/4" for event in progress_events)
    assert any("分析偏好" in event["details"]["line"] for event in progress_events)
    assert any("阶段完成" in event["details"]["line"] for event in progress_events)


def test_parser_rejects_conflicting_xhs_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-xhs", "--no-xhs"])


def test_parser_rejects_conflicting_douyin_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-douyin", "--no-douyin"])


def test_parser_rejects_conflicting_youtube_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-youtube", "--no-youtube"])


# ── Reused-cookie live validation (init-progress spec Phase 3) ──────────────


def _final_status(missing: list[str] | None = None) -> dict:
    return {
        "provider": "deepseek",
        "missing": list(missing or []),
        "has_cookie_inline": True,
        "has_cookie_file": True,
    }


def _reuse_summary(reused: list[str]) -> dict:
    return {"reused": reused, "skipped": [], "source": "/old/install"}


def _init_status(bilibili_check: str) -> dict:
    return {"prerequisites": {"bilibili_check": bilibili_check}}


def test_reused_cookie_stale_downgrades_to_needs_secrets() -> None:
    validated = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["bilibili.cookie", "data/bilibili_cookie.json"]),
        init_status=_init_status("failed"),
    )
    assert validated["reused_cookie_stale"] is True
    assert bootstrap.STALE_COOKIE_MISSING_ENTRY in validated["missing"]
    assert (
        bootstrap.STALE_COOKIE_MISSING_ENTRY
        == "bilibili.cookie (stale — reused cookie failed live validation)"
    )
    label = bootstrap.backend_healthy_label(validated)
    assert label == "needs_secrets"
    assert label != "complete"


def test_reused_cookie_valid_keeps_complete() -> None:
    validated = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["bilibili.cookie"]),
        init_status=_init_status("ok"),
    )
    assert "reused_cookie_stale" not in validated
    assert validated["missing"] == []
    assert bootstrap.backend_healthy_label(validated) == "complete"


def test_reused_cookie_unverifiable_probe_does_not_downgrade() -> None:
    # Backend unreachable / malformed payload / still "checking" → the
    # install.sh disclaimer branch stays; never claim staleness we didn't see.
    for payload in (None, {}, {"prerequisites": {}}, _init_status("checking")):
        validated = bootstrap.apply_reused_cookie_validation(
            _final_status(),
            reuse_summary=_reuse_summary(["data/bilibili_cookie.json"]),
            init_status=payload,
        )
        assert "reused_cookie_stale" not in validated
        assert bootstrap.backend_healthy_label(validated) == "complete"


def test_cookie_not_reused_skips_live_validation_fold() -> None:
    # This run reused only LLM keys — a failed bilibili probe must not be
    # attributed to a "reused stale cookie" (it was never reused).
    validated = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["llm.deepseek.api_key"]),
        init_status=_init_status("failed"),
    )
    assert "reused_cookie_stale" not in validated
    assert bootstrap.backend_healthy_label(validated) == "complete"


def test_backend_healthy_label_still_reports_plain_missing_secrets() -> None:
    assert (
        bootstrap.backend_healthy_label(_final_status(["bilibili.cookie"]))
        == "running_with_missing_secrets"
    )


def test_stale_entry_is_not_duplicated_on_refold() -> None:
    once = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["bilibili.cookie"]),
        init_status=_init_status("failed"),
    )
    twice = bootstrap.apply_reused_cookie_validation(
        once,
        reuse_summary=_reuse_summary(["bilibili.cookie"]),
        init_status=_init_status("failed"),
    )
    assert twice["missing"].count(bootstrap.STALE_COOKIE_MISSING_ENTRY) == 1
