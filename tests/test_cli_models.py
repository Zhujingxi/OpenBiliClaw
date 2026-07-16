"""Unified ordered model-route CLI contracts."""

from __future__ import annotations

import functools
import importlib
import sys
import tomllib
from dataclasses import replace
from types import FunctionType
from typing import TYPE_CHECKING, cast

import click
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
    from collections.abc import Iterable
    from pathlib import Path
    from types import ModuleType
    from typing import Any


_EXCEPTION_SECRET = "test-secret-exception-chain-never-retain"
_DASH_PREFIXED_EXCEPTION_SECRET = "--secret-token-never-retain"
_PARTIAL_FIRST_EXCEPTION_SECRET = "partial-first-secret-never-retain"
_PARTIAL_SECOND_EXCEPTION_SECRET = "partial-second-secret-never-retain"
_STORE_BOUNDARY_EXCEPTION_SECRET = "store-boundary-secret-never-retain"
_TERMINATED_API_KEY_LITERAL = "--api-key=literal-after-terminator"
_MODEL_CLI_SECRET_SENTINELS = (
    _EXCEPTION_SECRET,
    _DASH_PREFIXED_EXCEPTION_SECRET,
)
_PARTIAL_FAILURE_SECRET_SENTINELS = (
    _PARTIAL_FIRST_EXCEPTION_SECRET,
    _PARTIAL_SECOND_EXCEPTION_SECRET,
)


class _SanitizerAbort(BaseException):
    """Deterministic KeyboardInterrupt-like sanitizer failure for regression tests."""


class _FailingFinalWriteArgv(list[str]):
    """Raise on the cleanup write after accepting the pre-parser argv write."""

    def __init__(self, values: Iterable[str]) -> None:
        super().__init__(values)
        self.slice_writes = 0

    def __setitem__(self, key: int | slice, value: str | Iterable[str]) -> None:
        if isinstance(key, slice):
            self.slice_writes += 1
            if self.slice_writes == 2:
                raise _SanitizerAbort("forced final argv write abort")
        super().__setitem__(key, value)


def _exception_artifacts(error: BaseException) -> str:
    """Collect every reachable exception-frame value and callable closure."""
    artifacts: list[str] = []
    visited_values: set[int] = set()

    def visit(value: object, label: str) -> None:
        identity = id(value)
        if identity in visited_values:
            return
        visited_values.add(identity)
        try:
            artifacts.append(f"{label}={value!r}")
        except Exception:
            artifacts.append(f"{label}=<unrepresentable>")
        if isinstance(value, dict):
            for key, item in value.items():
                visit(key, f"{label}.key")
                visit(item, f"{label}[{key!r}]")
        elif isinstance(value, (list, tuple, set, frozenset)):
            for index, item in enumerate(value):
                visit(item, f"{label}[{index}]")
        elif isinstance(value, FunctionType):
            for index, cell in enumerate(value.__closure__ or ()):
                try:
                    visit(cell.cell_contents, f"{label}.closure[{index}]")
                except ValueError:
                    continue
        elif isinstance(value, functools.partial):
            visit(value.func, f"{label}.func")
            visit(value.args, f"{label}.args")
            visit(value.keywords or {}, f"{label}.keywords")
        elif isinstance(value, click.Context):
            visit(value.params, f"{label}.params")
            visit(value.args, f"{label}.args")
            visit(value._protected_args, f"{label}._protected_args")

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        visit(current, "exception")
        visit(current.args, "exception.args")
        for related in (current.__context__, current.__cause__):
            if related is not None:
                pending.append(related)
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            artifacts.append(f"frame={frame.f_code.co_filename}:{frame.f_code.co_name}")
            for name, value in frame.f_locals.items():
                visit(value, f"frame.{frame.f_code.co_name}.{name}")
            traceback = traceback.tb_next
    return "\n".join(artifacts)


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

    def __init__(
        self,
        path: Path,
        coordinator: FakeCoordinator,
        *,
        concurrent_models: ModelConfig | None = None,
    ) -> None:
        super().__init__(path, coordinator)
        self.conflicts = 0
        self.save_attempts = 0
        self.concurrent_models = concurrent_models

    async def save(self, request: ModelConfigSaveRequest) -> ModelConfigSaveResult:
        self.save_attempts += 1
        if self.conflicts == 0:
            self.conflicts += 1
            current = self.concurrent_models
            if current is None:
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


def test_models_edit_rejects_nonfinite_similarity_threshold_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))
    before = path.read_bytes()

    result = runner.invoke(
        app,
        ["models", "edit", "embed-a", "--similarity-threshold", "nan"],
    )

    assert result.exit_code == 1
    assert "models.embedding.settings.similarity_threshold" in result.output
    assert "invalid_embedding_similarity_threshold" in result.output
    assert path.read_bytes() == before


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


def test_models_remove_preserves_api_key_shaped_id_after_terminator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    models = _models()
    literal_id = "--api-key=route-id"
    literal_route = replace(
        models.chat.connections[1],
        id=literal_id,
        name="Literal API Key Shaped ID",
    )
    models = replace(
        models,
        chat=replace(
            models.chat,
            connections=(*models.chat.connections, literal_route),
        ),
    )
    path = _project_root(monkeypatch, tmp_path, models)
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))
    token_calls: list[int] = []

    def record_token_call(size: int) -> str:
        token_calls.append(size)
        return "unexpected-remove-handle"

    monkeypatch.setattr(module.secrets, "token_urlsafe", record_token_call)

    result = runner.invoke(app, ["models", "remove", "--", literal_id])

    assert result.exit_code == 0, result.output
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    remaining_ids = [item["id"] for item in parsed["models"]["chat"]["connections"]]
    assert literal_id not in remaining_ids
    assert token_calls == []
    assert module._INLINE_API_KEY_VAULT.pending_count() == 0


def test_models_edit_preserves_api_key_shaped_value_consumed_by_name_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))
    token_calls: list[int] = []

    def record_token_call(size: int) -> str:
        token_calls.append(size)
        return "unexpected-name-handle"

    monkeypatch.setattr(module.secrets, "token_urlsafe", record_token_call)
    literal_name = "--api-key=display-name"

    result = runner.invoke(
        app,
        ["models", "edit", "route-a", "--name", literal_name],
    )

    assert result.exit_code == 0, result.output
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["models"]["chat"]["connections"][0]["name"] == literal_name
    assert token_calls == []
    assert module._INLINE_API_KEY_VAULT.pending_count() == 0


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


@pytest.mark.parametrize(
    ("command_args", "failure_id"),
    [
        (
            [
                "models",
                "add",
                "--kind",
                "chat",
                "--id",
                "route-new",
                "--connection-type",
                "openai_compatible",
                "--preset",
                "custom",
                "--name",
                "New Route",
                "--model",
                "new-model",
                "--base-url",
                "https://new.example.test/v1",
                "--api-mode",
                "chat_completions",
                "--api-key",
                _EXCEPTION_SECRET,
            ],
            "add",
        ),
        (
            ["models", "edit", "route-a", "--api-key", _EXCEPTION_SECRET],
            "edit",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_model_api_key_is_absent_from_all_reachable_failure_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
    command_args: list[str],
    failure_id: str,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    coordinator = FakeCoordinator()
    coordinator.fail_build_with = _EXCEPTION_SECRET
    _install_service(module, monkeypatch, ModelConfigService(path, coordinator))

    result = runner.invoke(app, command_args)

    assert result.exit_code == 1
    assert failure_id in {"add", "edit"}
    assert _EXCEPTION_SECRET not in result.output
    assert result.exception is not None
    assert _EXCEPTION_SECRET not in _exception_artifacts(result.exception)


def test_model_api_key_vault_cleans_up_after_pre_callback_validation_exit(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    monkeypatch.setattr(
        module,
        "_build_model_config_service",
        lambda: pytest.fail("Click validation must stop before command invocation"),
    )

    invocations = (
        (app, ["models", "add"]),
        (module.models_app, ["add"]),
    )
    for target, prefix in invocations:
        result = runner.invoke(
            target,
            [
                *prefix,
                "--kind",
                "chat",
                "--api-key",
                _EXCEPTION_SECRET,
                "--position",
                "0",
            ],
        )

        assert result.exit_code == 2
        assert _EXCEPTION_SECRET not in result.output
        assert result.exception is not None
        assert _EXCEPTION_SECRET not in _exception_artifacts(result.exception)
        vault = getattr(module, "_INLINE_API_KEY_VAULT", None)
        assert vault is not None
        assert vault.pending_count() == 0


@pytest.mark.parametrize("target_kind", ["root", "subgroup"])
@pytest.mark.parametrize("argument_source", ["explicit", "sys_argv"])
@pytest.mark.parametrize("subcommand", ["add", "addd"])
@pytest.mark.parametrize("option_form", ["split", "equals", "dash_prefixed"])
def test_model_group_scrubs_api_key_before_all_parser_failures(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    target_kind: str,
    argument_source: str,
    subcommand: str,
    option_form: str,
) -> None:
    module = _models_module(runner)
    target = app if target_kind == "root" else module.models_app
    command_args = (["models"] if target_kind == "root" else []) + [subcommand]
    if option_form == "split":
        command_args.extend(["--api-key", _EXCEPTION_SECRET])
    elif option_form == "dash_prefixed":
        command_args.extend(["--api-key", _DASH_PREFIXED_EXCEPTION_SECRET])
    else:
        command_args.append(f"--api-key={_EXCEPTION_SECRET}")
    command_args.extend(["--kind", "chat", "--position", "0"])

    if argument_source == "explicit":
        result = runner.invoke(target, command_args)
        exception = result.exception
        output = result.output
        exit_code = result.exit_code
        retained_args = command_args
    else:
        monkeypatch.setattr(sys, "argv", ["model-cli-test", *command_args])
        command_args.clear()
        command = typer.main.get_command(target)
        with runner.isolation() as outstreams:
            with pytest.raises(SystemExit) as caught:
                command.main(args=None, prog_name="model-cli-test")
            output = outstreams[2].getvalue().decode("utf-8", errors="replace")
        exception = caught.value
        exit_code = cast("int", exception.code)
        retained_args = sys.argv[1:]

    assert exit_code == 2
    assert exception is not None
    artifacts = _exception_artifacts(exception)
    assert not any(value in output for value in _MODEL_CLI_SECRET_SENTINELS)
    assert not any(value in artifacts for value in _MODEL_CLI_SECRET_SENTINELS)
    assert not any(
        secret in value for secret in _MODEL_CLI_SECRET_SENTINELS for value in retained_args
    )
    assert not any(secret in value for secret in _MODEL_CLI_SECRET_SENTINELS for value in sys.argv)
    vault = getattr(module, "_INLINE_API_KEY_VAULT", None)
    assert vault is not None
    assert vault.pending_count() == 0


@pytest.mark.parametrize("target_kind", ["root", "subgroup"])
@pytest.mark.parametrize("argument_source", ["explicit", "sys_argv"])
@pytest.mark.parametrize("subcommand", ["add", "edit"])
def test_model_group_scrubs_api_key_after_group_terminator(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    target_kind: str,
    argument_source: str,
    subcommand: str,
) -> None:
    module = _models_module(runner)
    target = app if target_kind == "root" else module.models_app
    command_args = (["models"] if target_kind == "root" else []) + ["--", subcommand]
    if subcommand == "add":
        command_args.extend(
            [
                "--kind",
                "chat",
                "--api-key",
                _EXCEPTION_SECRET,
                "--position",
                "0",
            ]
        )
    else:
        command_args.extend(
            [
                "route-a",
                "--api-key",
                _EXCEPTION_SECRET,
                "--output-dimensionality",
                "-1",
            ]
        )

    if argument_source == "explicit":
        result = runner.invoke(target, command_args)
        exception = result.exception
        output = result.output
        exit_code = result.exit_code
        retained_args = command_args
    else:
        monkeypatch.setattr(sys, "argv", ["model-cli-test", *command_args])
        command_args.clear()
        command = typer.main.get_command(target)
        with runner.isolation() as outstreams:
            with pytest.raises(SystemExit) as caught:
                command.main(args=None, prog_name="model-cli-test")
            output = outstreams[2].getvalue().decode("utf-8", errors="replace")
        exception = caught.value
        exit_code = cast("int", exception.code)
        retained_args = sys.argv[1:]

    assert exit_code == 2
    assert exception is not None
    assert _EXCEPTION_SECRET not in output
    assert _EXCEPTION_SECRET not in _exception_artifacts(exception)
    assert all(_EXCEPTION_SECRET not in value for value in retained_args)
    assert all(_EXCEPTION_SECRET not in value for value in sys.argv)
    assert module._INLINE_API_KEY_VAULT.pending_count() == 0


def test_group_terminator_keeps_later_leaf_terminator_boundary(
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    command_args = [
        "models",
        "--",
        "add",
        "--kind",
        "chat",
        "--api-key",
        _EXCEPTION_SECRET,
        "--",
        _TERMINATED_API_KEY_LITERAL,
    ]

    result = runner.invoke(app, command_args)

    assert result.exit_code == 2
    assert result.exception is not None
    assert _EXCEPTION_SECRET not in result.output
    assert _EXCEPTION_SECRET not in _exception_artifacts(result.exception)
    assert all(_EXCEPTION_SECRET not in value for value in command_args)
    assert _TERMINATED_API_KEY_LITERAL in command_args
    assert module._INLINE_API_KEY_VAULT.pending_count() == 0


@pytest.mark.parametrize("argument_source", ["explicit", "sys_argv"])
def test_model_group_cleans_partial_vault_when_sanitizer_aborts(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    argument_source: str,
) -> None:
    module = _models_module(runner)
    vault = getattr(module, "_INLINE_API_KEY_VAULT", None)
    assert vault is not None
    assert vault.pending_count() == 0
    token_calls = 0

    def fail_second_token(_size: int) -> str:
        nonlocal token_calls
        token_calls += 1
        if token_calls == 1:
            return "partial-first-handle"
        raise _SanitizerAbort("forced sanitizer abort")

    monkeypatch.setattr(module.secrets, "token_urlsafe", fail_second_token)
    command_args = [
        "addd",
        "--api-key",
        _PARTIAL_FIRST_EXCEPTION_SECRET,
        "--api-key",
        _PARTIAL_SECOND_EXCEPTION_SECRET,
    ]

    if argument_source == "explicit":
        with pytest.raises(_SanitizerAbort) as caught:
            runner.invoke(module.models_app, command_args)
        retained_args = command_args
    else:
        monkeypatch.setattr(sys, "argv", ["model-cli-test", *command_args])
        command_args.clear()
        command = typer.main.get_command(module.models_app)
        with runner.isolation(), pytest.raises(_SanitizerAbort) as caught:
            command.main(args=None, prog_name="model-cli-test")
        retained_args = sys.argv[1:]

    try:
        artifacts = _exception_artifacts(caught.value)
        assert not any(value in artifacts for value in _PARTIAL_FAILURE_SECRET_SENTINELS)
        assert not any(
            secret in value
            for secret in _PARTIAL_FAILURE_SECRET_SENTINELS
            for value in retained_args
        )
        assert not any(
            secret in value for secret in _PARTIAL_FAILURE_SECRET_SENTINELS for value in sys.argv
        )
        assert vault.pending_count() == 0
    finally:
        vault.discard(module._InlineApiKeyHandle("partial-first-handle"))


def test_inline_api_key_vault_store_clears_raw_local_after_assignment_abort(
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    vault = module._InlineApiKeyVault()
    handle = module._InlineApiKeyHandle("store-boundary-handle")
    secret_holder: list[str | None] = [_STORE_BOUNDARY_EXCEPTION_SECRET]
    assert vault.reserve(handle)
    store_code = module._InlineApiKeyVault.store.__code__
    injection_observed = False

    def abort_after_assignment(frame: object, event: str, _arg: object) -> object:
        nonlocal injection_observed
        if getattr(frame, "f_code", None) is store_code:
            frame.f_trace_opcodes = True
            if (
                event == "opcode"
                and frame.f_locals.get("secret_value") == _STORE_BOUNDARY_EXCEPTION_SECRET
            ):
                injection_observed = True
                raise _SanitizerAbort("forced post-assignment store abort")
        return abort_after_assignment

    sys.settrace(abort_after_assignment)
    try:
        with pytest.raises(_SanitizerAbort) as caught:
            vault.store(handle, secret_holder)
    finally:
        sys.settrace(None)

    assert injection_observed
    assert secret_holder == [None]
    assert vault.pending_count() == 0
    assert _STORE_BOUNDARY_EXCEPTION_SECRET not in _exception_artifacts(caught.value)


@pytest.mark.parametrize("target_kind", ["root", "subgroup"])
@pytest.mark.parametrize("argument_source", ["explicit", "sys_argv"])
def test_malformed_model_scanner_stops_at_terminator(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    target_kind: str,
    argument_source: str,
) -> None:
    module = _models_module(runner)
    target = app if target_kind == "root" else module.models_app
    command_args = (["models"] if target_kind == "root" else []) + [
        "addd",
        "--api-key",
        _EXCEPTION_SECRET,
        "--",
        _TERMINATED_API_KEY_LITERAL,
    ]

    if argument_source == "explicit":
        result = runner.invoke(target, command_args)
        exception = result.exception
        output = result.output
        exit_code = result.exit_code
        retained_args = command_args
    else:
        monkeypatch.setattr(sys, "argv", ["model-cli-test", *command_args])
        command_args.clear()
        command = typer.main.get_command(target)
        with runner.isolation() as outstreams:
            with pytest.raises(SystemExit) as caught:
                command.main(args=None, prog_name="model-cli-test")
            output = outstreams[2].getvalue().decode("utf-8", errors="replace")
        exception = caught.value
        exit_code = cast("int", exception.code)
        retained_args = sys.argv[1:]

    assert exit_code == 2
    assert exception is not None
    assert _EXCEPTION_SECRET not in output
    assert _EXCEPTION_SECRET not in _exception_artifacts(exception)
    assert all(_EXCEPTION_SECRET not in value for value in retained_args)
    assert _TERMINATED_API_KEY_LITERAL in retained_args
    assert module._INLINE_API_KEY_VAULT.pending_count() == 0


@pytest.mark.parametrize("failure_site", ["redaction", "sys_argv_writeback"])
def test_model_group_discards_handles_when_final_cleanup_aborts(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    failure_site: str,
) -> None:
    module = _models_module(runner)
    vault = getattr(module, "_INLINE_API_KEY_VAULT", None)
    assert vault is not None
    assert vault.pending_count() == 0
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _size: "finalizer-handle")

    if failure_site == "redaction":

        def abort_redaction(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise _SanitizerAbort("forced final redaction abort")

        monkeypatch.setattr(module, "_redact_inline_api_key_args", abort_redaction)
        retained_args: list[str] = [
            "addd",
            "--api-key",
            _EXCEPTION_SECRET,
        ]
        with pytest.raises(_SanitizerAbort) as caught:
            runner.invoke(module.models_app, retained_args)
    else:
        retained_args = _FailingFinalWriteArgv(
            ["model-cli-test", "addd", "--api-key", _EXCEPTION_SECRET]
        )
        monkeypatch.setattr(sys, "argv", retained_args)
        command = typer.main.get_command(module.models_app)
        with runner.isolation(), pytest.raises(_SanitizerAbort) as caught:
            command.main(args=None, prog_name="model-cli-test")

    try:
        assert _EXCEPTION_SECRET not in _exception_artifacts(caught.value)
        assert all(_EXCEPTION_SECRET not in value for value in retained_args)
        assert vault.pending_count() == 0
    finally:
        vault.discard(module._InlineApiKeyHandle("finalizer-handle"))


def test_model_group_preserves_none_vs_explicit_base_main_forwarding(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    forwarded_args: list[object] = []

    def capture_base_main(_self: object, *args: object, **kwargs: object) -> None:
        del args
        forwarded_args.append(kwargs.get("args"))

    monkeypatch.setattr(module.TyperGroup, "main", capture_base_main)
    command = typer.main.get_command(module.models_app)
    monkeypatch.setattr(sys, "argv", ["model-cli-test", "addd"])

    command.main(args=None, prog_name="model-cli-test")
    explicit_args = ["addd"]
    command.main(args=explicit_args, prog_name="model-cli-test")

    assert forwarded_args == [None, explicit_args]
    assert forwarded_args[1] is explicit_args


def test_root_group_preserves_unrelated_api_key_option(
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    unrelated_app = typer.Typer(cls=module.SecretSafeTyperGroup)
    observed: list[str] = []

    @unrelated_app.callback()
    def unrelated_root(api_key: str = typer.Option(..., "--api-key")) -> None:
        observed.append(api_key)

    @unrelated_app.command("other")
    def unrelated_command() -> None:
        return None

    result = runner.invoke(
        unrelated_app,
        ["--api-key", _EXCEPTION_SECRET, "other"],
    )

    assert result.exit_code == 0, result.output
    assert observed == [_EXCEPTION_SECRET]
    vault = getattr(module, "_INLINE_API_KEY_VAULT", None)
    assert vault is not None
    assert vault.pending_count() == 0


def test_root_group_scrubbing_skips_top_level_option_values(
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    command_args = [
        "--log-level",
        "INFO",
        "models",
        "addd",
        "--api-key",
        _EXCEPTION_SECRET,
    ]

    result = runner.invoke(app, command_args)

    assert result.exit_code == 2
    assert result.exception is not None
    assert _EXCEPTION_SECRET not in result.output
    assert _EXCEPTION_SECRET not in _exception_artifacts(result.exception)
    assert all(_EXCEPTION_SECRET not in value for value in command_args)
    vault = getattr(module, "_INLINE_API_KEY_VAULT", None)
    assert vault is not None
    assert vault.pending_count() == 0


def test_model_safe_boundary_drops_recursive_secret_exception_state(
    runner: CliRunner,
) -> None:
    module = _models_module(runner)

    def fail_with_secret() -> None:
        options = module.RecordOptions(api_key=_EXCEPTION_SECRET)
        raise RuntimeError(_EXCEPTION_SECRET, options)

    with pytest.raises(typer.Exit) as caught:
        module._run_safe(fail_with_secret)

    surfaced = caught.value
    assert surfaced.exit_code == 1
    assert surfaced.__context__ is None
    assert surfaced.__cause__ is None
    assert _EXCEPTION_SECRET not in _exception_artifacts(surfaced)


def test_record_options_repr_excludes_inline_api_key(runner: CliRunner) -> None:
    module = _models_module(runner)

    rendered = repr(module.RecordOptions(api_key=_EXCEPTION_SECRET))

    assert _EXCEPTION_SECRET not in rendered
    assert "api_key=" not in rendered


@pytest.mark.parametrize("editor_name", ["guided_chat_editor", "guided_embedding_editor"])
def test_guided_editors_use_the_secret_safe_exception_boundary(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    editor_name: str,
) -> None:
    module = _models_module(runner)
    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)

    def fail_service() -> ModelConfigService:
        raise RuntimeError(_EXCEPTION_SECRET)

    monkeypatch.setattr(module, "_build_model_config_service", fail_service)

    with pytest.raises(typer.Exit) as caught:
        getattr(module, editor_name)()

    surfaced = caught.value
    assert surfaced.exit_code == 1
    assert surfaced.__context__ is None
    assert surfaced.__cause__ is None
    assert _EXCEPTION_SECRET not in _exception_artifacts(surfaced)


def test_setup_embedding_noninteractive_never_enters_prompting_editor(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False)

    def unexpected_editor() -> None:
        calls.append("editor")
        raise RuntimeError(_EXCEPTION_SECRET)

    monkeypatch.setattr(module, "guided_embedding_editor", unexpected_editor)
    monkeypatch.setattr(
        module.typer,
        "prompt",
        lambda *_args, **_kwargs: pytest.fail("non-TTY setup must never prompt"),
    )

    result = runner.invoke(app, ["setup-embedding"])

    assert result.exit_code == 1
    assert calls == []
    assert result.output.strip() == (
        "Error: setup-embedding requires an interactive terminal. "
        "For automation, use `openbiliclaw models add --kind embedding` "
        "with explicit options."
    )
    assert _EXCEPTION_SECRET not in result.output
    if result.exception is not None:
        assert _EXCEPTION_SECRET not in _exception_artifacts(result.exception)


def test_setup_embedding_delegates_to_native_embedding_route_editor(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    module = _models_module(runner)
    calls: list[bool] = []
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)
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
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)
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


def _prepare_guided_chat_edit(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stable_id: str = "route-a",
) -> None:
    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        module,
        "_guided_type_and_preset",
        lambda *_args: ("openai_compatible", "custom"),
    )
    monkeypatch.setattr(
        module,
        "_guided_record_options",
        lambda *_args, **_kwargs: module.RecordOptions(
            connection_type="openai_compatible",
            preset="custom",
            name="Guided A",
            model="guided-chat-a",
            base_url="https://guided-a.example.test/v1",
            api_mode="chat_completions",
            api_key_env="GUIDED_CHAT_KEY",
        ),
    )

    def fake_prompt(label: str, **kwargs: object) -> str:
        values = {
            "Stable connection ID": stable_id,
            "Connection name": "Guided A",
        }
        return values.get(label, str(kwargs.get("default", "")))

    monkeypatch.setattr(module.typer, "prompt", fake_prompt)


def test_guided_chat_edit_preserves_every_existing_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    _install_service(module, monkeypatch, ModelConfigService(path, FakeCoordinator()))
    _prepare_guided_chat_edit(module, monkeypatch)

    module.guided_chat_editor()

    rows = tomllib.loads(path.read_text(encoding="utf-8"))["models"]["chat"]["connections"]
    assert [row["id"] for row in rows] == ["route-a", "route-b"]
    assert rows[0]["name"] == "Guided A"
    assert rows[0]["model"] == "guided-chat-a"
    assert rows[1]["name"] == "Gateway B"


def test_guided_chat_conflict_retry_preserves_concurrently_added_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
) -> None:
    path = _project_root(monkeypatch, tmp_path, _models())
    module = _models_module(runner)
    service = ConflictOnceService(path, FakeCoordinator())
    _install_service(module, monkeypatch, service)
    _prepare_guided_chat_edit(module, monkeypatch)

    module.guided_chat_editor()

    rows = tomllib.loads(path.read_text(encoding="utf-8"))["models"]["chat"]["connections"]
    assert service.conflicts == 1
    assert [row["id"] for row in rows] == ["route-a", "route-b", "concurrent-route"]
    assert rows[0]["name"] == "Guided A"


def test_guided_chat_edit_conflict_does_not_resurrect_deleted_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initial = _models()
    concurrent_b = replace(initial.chat.connections[1], name="Concurrent B")
    concurrent_models = replace(
        initial,
        chat=replace(initial.chat, connections=(concurrent_b,)),
    )
    path = _project_root(monkeypatch, tmp_path, initial)
    module = _models_module(runner)
    service = ConflictOnceService(
        path,
        FakeCoordinator(),
        concurrent_models=concurrent_models,
    )
    _install_service(module, monkeypatch, service)
    _prepare_guided_chat_edit(module, monkeypatch, stable_id="route-a")

    with pytest.raises(typer.Exit):
        module.guided_chat_editor()

    captured = capsys.readouterr()
    rows = tomllib.loads(path.read_text(encoding="utf-8"))["models"]["chat"]["connections"]
    assert service.save_attempts == 1
    assert [row["id"] for row in rows] == ["route-b"]
    assert rows[0]["name"] == "Concurrent B"
    assert "concurrently" in captured.err


def test_guided_chat_add_conflict_does_not_overwrite_concurrent_same_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: CliRunner,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initial = _models()
    other_writer = ChatConnection(
        id="route-new",
        name="Other Writer",
        type="ollama",
        model="other-writer-model",
        base_url="http://127.0.0.1:11434/v1",
    )
    concurrent_models = replace(
        initial,
        chat=replace(
            initial.chat,
            connections=(*initial.chat.connections, other_writer),
        ),
    )
    path = _project_root(monkeypatch, tmp_path, initial)
    module = _models_module(runner)
    service = ConflictOnceService(
        path,
        FakeCoordinator(),
        concurrent_models=concurrent_models,
    )
    _install_service(module, monkeypatch, service)
    _prepare_guided_chat_edit(module, monkeypatch, stable_id="route-new")

    with pytest.raises(typer.Exit):
        module.guided_chat_editor()

    captured = capsys.readouterr()
    rows = tomllib.loads(path.read_text(encoding="utf-8"))["models"]["chat"]["connections"]
    assert service.save_attempts == 1
    assert [row["id"] for row in rows] == ["route-a", "route-b", "route-new"]
    assert rows[-1]["name"] == "Other Writer"
    assert rows[-1]["model"] == "other-writer-model"
    assert "concurrently" in captured.err


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
