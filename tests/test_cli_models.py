"""Unified ordered model-route CLI contracts."""

from __future__ import annotations

import importlib
import tomllib
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
import typer
from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw import config as config_module
from openbiliclaw.cli import app
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
    ModelConfigSnapshot,
    render_model_config,
)
from openbiliclaw.model_config.service import (
    ModelConfigProbeResult,
    ModelConfigSaveRequest,
    ModelConfigSaveResult,
    ModelConfigService,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType
    from typing import Any


class FakeCoordinator:
    """Network-free runtime coordinator used by CLI persistence tests."""

    def __init__(self) -> None:
        self._current: object | None = object()
        self.probe_ids: list[str] = []
        self.fail_build_with = ""

    @property
    def current_model_candidate(self) -> object | None:
        return self._current

    async def build_model_candidate(self, models: ModelConfig, revision: str) -> object:
        if self.fail_build_with:
            raise RuntimeError(self.fail_build_with)
        return (models, revision)

    def restage_model_candidate(
        self,
        candidate: object,
        models: ModelConfig,
        revision: str,
    ) -> object:
        return candidate if candidate == (models, revision) else (models, revision)

    async def swap_model_candidate(self, candidate: object) -> object | None:
        previous = self._current
        self._current = candidate
        return previous

    async def restore_model_candidate(self, candidate: object | None) -> None:
        self._current = candidate

    async def probe_model_draft(
        self,
        draft: ChatConnection | EmbeddingProviderConfig,
        settings: EmbeddingModelSettings | None = None,
    ) -> ModelConfigProbeResult:
        self.probe_ids.append(draft.id)
        return ModelConfigProbeResult(
            ok=True,
            connection_id=draft.id,
            capability="chat" if isinstance(draft, ChatConnection) else "embedding",
            observed_dimension=(settings.output_dimensionality if settings is not None else 0),
        )


class ConflictOnceService(ModelConfigService):
    """Publish one concurrent route before returning the first save conflict."""

    def __init__(self, path: Path, coordinator: FakeCoordinator) -> None:
        super().__init__(path, coordinator)
        self.conflicts = 0

    async def save(self, request: ModelConfigSaveRequest) -> ModelConfigSaveResult:
        if self.conflicts == 0:
            self.conflicts += 1
            current = _models()
            concurrent = ChatConnection(
                id="concurrent-route",
                name="Concurrent Route",
                type="ollama",
                model="qwen2.5:7b",
                base_url="http://127.0.0.1:11434/v1",
            )
            current = replace(
                current,
                chat=replace(
                    current.chat,
                    connections=(*current.chat.connections, concurrent),
                ),
            )
            _write_native(self.path, current)
            return ModelConfigSaveResult(
                ok=False,
                snapshot=self.read(),
                conflict=True,
            )
        return await super().save(request)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    return CliRunner()


def _models() -> ModelConfig:
    return ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="route-a",
                    name="Gateway A",
                    type="openai_compatible",
                    preset="custom",
                    model="chat-a",
                    base_url="https://a.example.test/v1",
                    credential=CredentialConfig(
                        source="inline",
                        value="test-secret-inline-never-print",
                    ),
                    api_mode="chat_completions",
                ),
                ChatConnection(
                    id="route-b",
                    name="Gateway B",
                    type="openai_compatible",
                    preset="custom",
                    model="chat-b",
                    base_url="https://b.example.test/v1",
                    credential=CredentialConfig(source="env", value="MODEL_KEY_B"),
                    api_mode="chat_completions",
                ),
            ),
            concurrency=4,
            timeout_seconds=300,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=True,
            settings=EmbeddingModelSettings(
                model="shared-embedding",
                output_dimensionality=768,
                similarity_threshold=0.75,
                multimodal_enabled=False,
            ),
            providers=(
                EmbeddingProviderConfig(
                    id="embed-a",
                    name="Embedding A",
                    type="openai_compatible",
                    preset="custom",
                    base_url="https://embed.example.test/v1",
                    credential=CredentialConfig(source="env", value="EMBEDDING_KEY"),
                ),
            ),
        ),
    )


def _write_native(path: Path, models: ModelConfig) -> None:
    path.write_text("\n".join(render_model_config(models)) + "\n", encoding="utf-8")


def _project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, models: ModelConfig) -> Path:
    path = tmp_path / "config.toml"
    _write_native(path, models)
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    return path


def _models_module(runner: CliRunner) -> ModuleType:
    prerequisite = runner.invoke(app, ["models", "--help"])
    assert prerequisite.exit_code == 0, prerequisite.output
    return importlib.import_module("openbiliclaw.cli_models")


def _install_service(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    service: ModelConfigService,
) -> None:
    monkeypatch.setattr(module, "_build_model_config_service", lambda: service)


def test_models_help_registers_the_six_route_commands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["models", "--help"])

    assert result.exit_code == 0
    for command in ("list", "add", "edit", "remove", "move", "probe"):
        assert command in result.output

    add_help = runner.invoke(app, ["models", "add", "--help"])
    assert add_help.exit_code == 0
    root_command = typer.main.get_command(app)
    models_command = cast("Any", root_command).commands["models"]
    add_command = models_command.commands["add"]
    option_names = {
        option for parameter in add_command.params for option in getattr(parameter, "opts", ())
    }
    for option in (
        "--kind",
        "--connection-type",
        "--preset",
        "--name",
        "--model",
        "--base-url",
        "--api-mode",
        "--api-key",
        "--api-key-env",
        "--credential-ref",
        "--output-dimensionality",
        "--similarity-threshold",
    ):
        assert option in option_names


def test_models_list_renders_order_shared_settings_and_safe_credential_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    monkeypatch.setattr(
        module,
        "_circuit_statuses",
        lambda: {"route-a": "closed", "embed-a": "open"},
    )

    result = runner.invoke(app, ["models", "list"])

    assert result.exit_code == 0, result.output
    assert "1" in result.output and "primary" in result.output and "route-a" in result.output
    assert "2" in result.output and "fallback_1" in result.output and "route-b" in result.output
    assert result.output.index("route-a") < result.output.index("route-b")
    assert "shared-embedding" in result.output
    assert "embed-a" in result.output
    assert "inline" in result.output
    assert "env:MODEL_KEY_B" in result.output
    assert "circuit=closed" in result.output
    assert "circuit=open" in result.output
    assert "circuit=unknown" in result.output
    assert "test-secret-inline-never-print" not in result.output


def test_models_add_allows_two_more_same_type_routes_with_distinct_stable_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    coordinator = FakeCoordinator()
    _install_service(module, monkeypatch, ModelConfigService(path, coordinator))
    monkeypatch.setenv("MODEL_KEY_C", "test-secret-c-never-print")
    monkeypatch.setenv("MODEL_KEY_D", "test-secret-d-never-print")

    common = [
        "--kind",
        "chat",
        "--connection-type",
        "openai_compatible",
        "--preset",
        "custom",
        "--api-mode",
        "chat_completions",
    ]
    first = runner.invoke(
        app,
        [
            "models",
            "add",
            *common,
            "--id",
            "route-c",
            "--name",
            "Gateway C",
            "--model",
            "chat-c",
            "--base-url",
            "https://c.example.test/v1",
            "--api-key-env",
            "MODEL_KEY_C",
        ],
    )
    second = runner.invoke(
        app,
        [
            "models",
            "add",
            *common,
            "--id",
            "route-d",
            "--name",
            "Gateway D",
            "--model",
            "chat-d",
            "--base-url",
            "https://d.example.test/v1",
            "--api-key-env",
            "MODEL_KEY_D",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    connections = parsed["models"]["chat"]["connections"]
    assert [item["id"] for item in connections][-2:] == ["route-c", "route-d"]
    assert connections[-2]["type"] == connections[-1]["type"] == "openai_compatible"
    assert connections[-2]["api_key_env"] == "MODEL_KEY_C"
    combined = first.output + second.output
    assert "test-secret-c-never-print" not in combined
    assert "test-secret-d-never-print" not in combined


def test_models_add_embedding_updates_one_shared_space_and_appends_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))

    result = runner.invoke(
        app,
        [
            "models",
            "add",
            "--kind",
            "embedding",
            "--id",
            "embed-local",
            "--connection-type",
            "ollama",
            "--name",
            "Local Embedding",
            "--model",
            "bge-m3",
            "--base-url",
            "http://127.0.0.1:11434/v1",
            "--output-dimensionality",
            "1024",
            "--similarity-threshold",
            "0.82",
            "--no-multimodal",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))["models"]["embedding"]
    assert parsed["settings"] == {
        "model": "bge-m3",
        "output_dimensionality": 1024,
        "similarity_threshold": 0.82,
        "multimodal_enabled": False,
    }
    assert [item["id"] for item in parsed["providers"]] == ["embed-a", "embed-local"]
    assert all("model" not in item for item in parsed["providers"])


def test_models_edit_move_remove_use_stable_id_and_guard_the_final_chat_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))

    edited = runner.invoke(app, ["models", "edit", "route-b", "--name", "Edited B"])
    moved = runner.invoke(app, ["models", "move", "route-b", "--position", "1"])
    removed = runner.invoke(app, ["models", "remove", "route-a"])
    final_guard = runner.invoke(app, ["models", "remove", "route-b"])

    assert edited.exit_code == 0, edited.output
    assert moved.exit_code == 0, moved.output
    assert removed.exit_code == 0, removed.output
    assert final_guard.exit_code == 1
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert [item["id"] for item in parsed["models"]["chat"]["connections"]] == ["route-b"]
    assert parsed["models"]["chat"]["connections"][0]["name"] == "Edited B"
    assert "final Chat connection" in final_guard.output


def test_models_probe_captures_and_revalidates_the_exact_selected_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    coordinator = FakeCoordinator()
    _install_service(module, monkeypatch, ModelConfigService(path, coordinator))

    chat_result = runner.invoke(app, ["models", "probe", "route-b"])
    embedding_result = runner.invoke(app, ["models", "probe", "embed-a"])

    assert chat_result.exit_code == 0, chat_result.output
    assert embedding_result.exit_code == 0, embedding_result.output
    assert coordinator.probe_ids == ["route-b", "embed-a"]
    assert "route-b" in chat_result.output
    assert "embed-a" in embedding_result.output
    assert "observed_dimension=768" in embedding_result.output
    combined = chat_result.output + embedding_result.output
    assert "test-secret-inline-never-print" not in combined


def test_models_edit_rebases_once_on_stale_revision_without_losing_concurrent_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    service = ConflictOnceService(path, FakeCoordinator())
    _install_service(module, monkeypatch, service)

    result = runner.invoke(app, ["models", "edit", "route-a", "--name", "Rebased A"])

    assert result.exit_code == 0, result.output
    assert service.conflicts == 1
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = parsed["models"]["chat"]["connections"]
    assert [item["id"] for item in rows] == ["route-a", "route-b", "concurrent-route"]
    assert rows[0]["name"] == "Rebased A"
    assert "revision changed" in result.output.lower()
    persisted = path.read_text(encoding="utf-8")
    assert "test-secret-inline-never-print" in persisted
    assert "cli-inline-credential-preserved" not in persisted


def test_models_list_exposes_safe_migration_warning_and_edit_accepts_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
default_provider = "deepseek"
concurrency = 4
timeout = 300

[llm.deepseek]
api_key = "test-secret-legacy-never-print"
model = "deepseek-chat"
base_url = "https://api.deepseek.com"

[llm.soul]
provider = "deepseek"
model = "special-model"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    module = _models_module(runner)
    service = ModelConfigService(path, FakeCoordinator(), environment={})
    _install_service(module, monkeypatch, service)
    snapshot = service.read()
    assert snapshot.migration is not None
    issue = next(item for item in snapshot.migration.issues if item.severity == "blocking")
    connection_id = snapshot.models.chat.connections[0].id

    listed = runner.invoke(app, ["models", "list"])
    resolved = runner.invoke(
        app,
        [
            "models",
            "edit",
            connection_id,
            "--name",
            "Migrated DeepSeek",
            "--resolve",
            f"{issue.id}=accept_global_route",
        ],
    )

    assert listed.exit_code == 0, listed.output
    assert "migration" in listed.output.lower()
    assert issue.id in listed.output
    assert "accept_global_route" in listed.output
    assert "test-secret-legacy-never-print" not in listed.output
    assert resolved.exit_code == 0, resolved.output
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "models" in parsed and "llm" not in parsed
    assert parsed["models"]["chat"]["connections"][0]["name"] == "Migrated DeepSeek"
    assert "test-secret-legacy-never-print" not in resolved.output


def test_models_add_noninteractive_missing_required_fields_fails_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))

    result = runner.invoke(
        app,
        [
            "models",
            "add",
            "--kind",
            "chat",
            "--id",
            "incomplete",
            "--connection-type",
            "openai_compatible",
            "--preset",
            "custom",
        ],
    )

    assert result.exit_code == 1
    assert "non-interactive" in result.output.lower()
    assert "model" in result.output.lower()
    assert "API Key" not in result.output


def test_models_save_failure_never_echoes_secret_bearing_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    coordinator = FakeCoordinator()
    coordinator.fail_build_with = "upstream test-secret-exception-never-print"
    _install_service(module, monkeypatch, ModelConfigService(path, coordinator))

    result = runner.invoke(app, ["models", "edit", "route-a", "--name", "Safe Failure"])

    assert result.exit_code == 1
    assert "candidate" in result.output.lower()
    assert "test-secret-exception-never-print" not in result.output
    assert "test-secret-inline-never-print" not in result.output


def test_setup_embedding_delegates_to_native_embedding_route_editor(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    calls: list[bool] = []
    monkeypatch.setattr(module, "guided_embedding_editor", lambda: calls.append(True))
    monkeypatch.setattr(
        cli_module,
        "_save_embedding_config",
        lambda **_: pytest.fail("legacy [llm.embedding] writer must not be called"),
        raising=False,
    )

    result = runner.invoke(app, ["setup-embedding"])

    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_guided_runtime_setup_uses_native_chat_then_embedding_editors(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    calls: list[str] = []
    monkeypatch.setattr(module, "guided_chat_editor", lambda: calls.append("chat"))
    monkeypatch.setattr(module, "guided_embedding_editor", lambda: calls.append("embedding"))
    monkeypatch.setattr(
        cli_module,
        "_save_runtime_provider_config",
        lambda *_, **__: pytest.fail("legacy provider writer must not be called"),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_save_embedding_config",
        lambda **_: pytest.fail("legacy embedding writer must not be called"),
        raising=False,
    )

    cli_module._interactive_runtime_config_setup()

    assert calls == ["chat", "embedding"]


def test_guided_chat_prompts_only_fields_exposed_by_selected_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    service = ModelConfigService(path, FakeCoordinator())
    _install_service(module, monkeypatch, service)
    monkeypatch.setattr(
        module,
        "_guided_type_and_preset",
        lambda *_args: ("codex_oauth", ""),
    )
    prompts: list[str] = []

    def fake_prompt(label: str, **kwargs: object) -> str:
        prompts.append(label)
        values = {
            "Stable connection ID": "codex-main",
            "Connection name": "Codex",
            "Model": "gpt-5",
            "Base URL": "https://must-not-be-requested.example.test",
        }
        return values.get(label, str(kwargs.get("default", "")))

    async def fake_save(*_args: object, **_kwargs: object) -> ModelConfigSnapshot:
        return service.read()

    monkeypatch.setattr(module.typer, "prompt", fake_prompt)
    monkeypatch.setattr(module, "_save_with_rebase", fake_save)

    module.guided_chat_editor()

    assert prompts == ["Stable connection ID", "Connection name", "Model"]
