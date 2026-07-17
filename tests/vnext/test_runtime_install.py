from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest


def _load_bootstrap_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("vnext_agent_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def _read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def test_local_environment_is_private_atomic_and_idempotent(tmp_path: Path) -> None:
    first = bootstrap.ensure_local_runtime_environment(
        tmp_path,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="proxy-secret",
    )
    env_path = tmp_path / ".env"
    first_bytes = env_path.read_bytes()

    second = bootstrap.ensure_local_runtime_environment(
        tmp_path,
        litellm_base_url="https://ignored.example/v1",
        litellm_api_key="ignored-secret",
    )

    assert env_path.read_bytes() == first_bytes
    assert first == second
    values = _read_env(env_path)
    assert values["OPENBILICLAW_LITELLM_BASE_URL"] == "https://models.example/v1"
    assert values["OPENBILICLAW_LITELLM_API_KEY"] == "proxy-secret"
    assert len(values["OPENBILICLAW_ACCESS_TOKEN"]) >= 48
    assert len(values["OPENBILICLAW_SECRET_KEY"]) == 64
    assert values["OPENBILICLAW_DATABASE_URL"].endswith("/data/vnext/openbiliclaw.db")
    assert values["OPENBILICLAW_HUEY_PATH"].endswith("/data/vnext/huey.db")
    assert not list(tmp_path.glob(".env.tmp-*"))
    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_local_environment_requires_user_supplied_litellm(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="LiteLLM base URL and API key are required"):
        bootstrap.ensure_local_runtime_environment(
            tmp_path,
            litellm_base_url="",
            litellm_api_key="",
        )
    assert not (tmp_path / ".env").exists()


def test_local_environment_rejects_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("DO_NOT_TOUCH=1\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(outside)

    with pytest.raises(RuntimeError, match="symlink"):
        bootstrap.ensure_local_runtime_environment(
            tmp_path,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
        )
    assert outside.read_text(encoding="utf-8") == "DO_NOT_TOUCH=1\n"


def test_local_install_migrates_then_starts_api_and_worker_without_secret_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    processes: list[tuple[list[str], dict[str, str]]] = []
    timeline: list[str] = []

    def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        calls.append((command, env))
        timeline.append(command[-1])

    class Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def start_process(
        command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path
    ) -> Process:
        processes.append((command, env))
        timeline.append(command[-1])
        return Process(1000 + len(processes))

    result = bootstrap.install_local_runtime(
        tmp_path,
        host="127.0.0.1",
        port=8420,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="sentinel-proxy-secret",
        install_dependencies=False,
        run_command=run_command,
        start_process=start_process,
        readiness_probe=lambda *_args, **_kwargs: True,
        queue_probe=lambda *_args, **_kwargs: True,
    )

    assert calls[0][0][-2:] == ["db", "migrate"]
    assert calls[1][0][-1] == "doctor"
    assert timeline == ["migrate", "8420", "worker", "doctor"]
    assert [command[-1] for command, _env in processes] == ["8420", "worker"]
    assert processes[0][0][-4:] == ["--host", "127.0.0.1", "--port", "8420"]
    assert processes[1][0][-2:] == ["openbiliclaw", "worker"]
    assert all(env["OPENBILICLAW_HUEY_PATH"].endswith("huey.db") for _, env in processes)
    assert result.status == "complete"
    assert result.api_pid == 1001
    assert result.worker_pid == 1002
    pid_state = json.loads((tmp_path / "data/vnext/runtime-processes.json").read_text())
    assert pid_state == {"api": 1001, "worker": 1002}
    rendered = capsys.readouterr().out
    assert "sentinel-proxy-secret" not in rendered
    assert "https://models.example/v1" not in rendered


def test_local_install_propagates_migration_failure_and_starts_nothing(tmp_path: Path) -> None:
    started: list[list[str]] = []

    def fail_migration(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=fail_migration,
            start_process=lambda command, **_kwargs: started.append(command),
            readiness_probe=lambda *_args, **_kwargs: True,
            queue_probe=lambda *_args, **_kwargs: True,
        )
    assert started == []


def test_local_install_fails_if_protected_readiness_fails_and_cleans_pid_state(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        commands.append(command)

    class Process:
        pid = 1234

    with pytest.raises(RuntimeError, match="protected readiness check failed"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=run_command,
            start_process=lambda *_args, **_kwargs: Process(),
            readiness_probe=lambda *_args, **_kwargs: False,
            queue_probe=lambda *_args, **_kwargs: True,
        )
    assert any(command[-1] == "doctor" for command in commands)
    assert not (tmp_path / "data/vnext/runtime-processes.json").exists()


def test_local_install_propagates_doctor_failure_and_cleans_processes(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        commands.append(command)
        if command[-1] == "doctor":
            raise RuntimeError("doctor failed")

    class Process:
        pid = 1234

    with pytest.raises(RuntimeError, match="doctor failed"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=run_command,
            start_process=lambda *_args, **_kwargs: Process(),
            readiness_probe=lambda *_args, **_kwargs: True,
            queue_probe=lambda *_args, **_kwargs: True,
        )
    assert commands[0][-1] == "migrate"
    assert commands[1][-1] == "doctor"
    assert not (tmp_path / "data/vnext/runtime-processes.json").exists()


def test_local_install_cleans_processes_when_worker_queue_never_opens(tmp_path: Path) -> None:
    class Process:
        pid = 1234

    with pytest.raises(RuntimeError, match="worker queue did not initialize"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=lambda *_args, **_kwargs: None,
            start_process=lambda *_args, **_kwargs: Process(),
            readiness_probe=lambda *_args, **_kwargs: True,
            queue_probe=lambda *_args, **_kwargs: False,
        )
    assert not (tmp_path / "data/vnext/runtime-processes.json").exists()


def test_compose_prefix_supports_source_and_prebuilt_distributions(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.prebuilt.yml").touch()
    assert bootstrap._compose_prefix(tmp_path) == [
        "docker",
        "compose",
        "-f",
        "docker-compose.prebuilt.yml",
    ]

    (tmp_path / "docker-compose.yml").touch()
    assert bootstrap._compose_prefix(tmp_path) == ["docker", "compose"]


def test_bootstrap_source_has_no_removed_feature_commands() -> None:
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    for command in (
        '"init"',
        '"models"',
        '"recommend"',
        '"profile"',
        '"setup-embedding"',
        '"serve-api"',
    ):
        assert command not in source
