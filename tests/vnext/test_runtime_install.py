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


class _LiveProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.exit_code = -15

    def kill(self) -> None:
        self.exit_code = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        assert self.exit_code is not None
        return self.exit_code


def _identity(pid: int):
    return bootstrap.ProcessIdentity(pid, f"start-{pid}", "/test/openbiliclaw", "a" * 64)


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


def test_copied_local_environment_rebinds_managed_paths_and_instance(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    original.mkdir()
    copied.mkdir()
    first = bootstrap.ensure_local_runtime_environment(
        original,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="proxy-secret",
    )
    (copied / ".env").write_bytes((original / ".env").read_bytes())

    rebound = bootstrap.ensure_local_runtime_environment(
        copied,
        litellm_base_url="https://ignored.example/v1",
        litellm_api_key="ignored-secret",
    )

    assert rebound["OPENBILICLAW_SECRET_KEY"] == first["OPENBILICLAW_SECRET_KEY"]
    assert rebound["OPENBILICLAW_ACCESS_TOKEN"] == first["OPENBILICLAW_ACCESS_TOKEN"]
    assert rebound["OPENBILICLAW_LITELLM_BASE_URL"] == first["OPENBILICLAW_LITELLM_BASE_URL"]
    assert rebound["OPENBILICLAW_LITELLM_API_KEY"] == first["OPENBILICLAW_LITELLM_API_KEY"]
    assert rebound["OPENBILICLAW_PROJECT_ROOT"] == str(copied.resolve())
    assert (
        rebound["OPENBILICLAW_INSTALLER_INSTANCE_ID"] != first["OPENBILICLAW_INSTALLER_INSTANCE_ID"]
    )
    assert rebound["OPENBILICLAW_DATABASE_URL"] == bootstrap._sqlite_url(
        copied / "data/vnext/openbiliclaw.db"
    )
    assert rebound["OPENBILICLAW_HUEY_PATH"] == str((copied / "data/vnext/huey.db").resolve())


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


def test_public_run_preserves_symlink_path_for_local_lifecycle_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    args = bootstrap.build_parser().parse_args(
        ["--project-dir", str(linked), "--mode", "local", "--skip-install", "--skip-start"]
    )

    def enter_local_lifecycle(project_dir: Path, **_kwargs: object) -> object:
        with bootstrap._lifecycle_lock(project_dir, timeout=1.0):
            raise AssertionError("symlinked project path entered local lifecycle")

    monkeypatch.setattr(bootstrap, "install_local_runtime", enter_local_lifecycle)

    with pytest.raises(RuntimeError, match="symlinked project path"):
        bootstrap.run(args)


def test_local_install_migrates_then_starts_api_and_worker_without_secret_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    processes: list[tuple[list[str], dict[str, str]]] = []
    timeline: list[str] = []

    def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        calls.append((command, env))
        timeline.append(command[-1])

    def start_process(
        command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path
    ) -> _LiveProcess:
        processes.append((command, env))
        timeline.append(command[-1])
        return _LiveProcess(1000 + len(processes))

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
        identity_probe=lambda pid: _identity(pid),
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
    assert pid_state["version"] == 2
    assert pid_state["project_root"] == str(tmp_path.resolve())
    assert pid_state["generation"] == 1
    assert pid_state["api"]["pid"] == 1001
    assert pid_state["worker"]["pid"] == 1002
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


def test_local_install_fails_if_protected_readiness_fails_and_retains_pid_state(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        commands.append(command)

    with pytest.raises(RuntimeError, match="protected readiness check failed"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=run_command,
            start_process=lambda *_args, **_kwargs: _LiveProcess(1234),
            readiness_probe=lambda *_args, **_kwargs: False,
            queue_probe=lambda *_args, **_kwargs: True,
            identity_probe=lambda pid: _identity(pid),
        )
    assert any(command[-1] == "doctor" for command in commands)
    assert (tmp_path / "data/vnext/runtime-processes.json").exists()


def test_local_install_propagates_doctor_failure_and_retains_process_state(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        commands.append(command)
        if command[-1] == "doctor":
            raise RuntimeError("doctor failed")

    with pytest.raises(RuntimeError, match="doctor failed"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=run_command,
            start_process=lambda *_args, **_kwargs: _LiveProcess(1234),
            readiness_probe=lambda *_args, **_kwargs: True,
            queue_probe=lambda *_args, **_kwargs: True,
            identity_probe=lambda pid: _identity(pid),
        )
    assert commands[0][-1] == "migrate"
    assert commands[1][-1] == "doctor"
    assert (tmp_path / "data/vnext/runtime-processes.json").exists()


def test_local_install_retains_process_state_when_worker_queue_never_opens(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="worker queue did not initialize"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=lambda *_args, **_kwargs: None,
            start_process=lambda *_args, **_kwargs: _LiveProcess(1234),
            readiness_probe=lambda *_args, **_kwargs: True,
            queue_probe=lambda *_args, **_kwargs: False,
            identity_probe=lambda pid: _identity(pid),
        )
    assert (tmp_path / "data/vnext/runtime-processes.json").exists()


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


def test_docker_skip_start_runs_one_shot_migration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> object:
        calls.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(bootstrap.subprocess, "run", run)

    result = bootstrap._install_docker_runtime(tmp_path, start=False)

    assert result.status == "prepared"
    assert calls == [["docker", "compose", "run", "--rm", "migrate"]]


def test_docker_install_requires_migration_api_and_worker_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> object:
        calls.append(command)

        class Result:
            returncode = 0
            stdout = (
                '[{"Service":"migrate","State":"exited","ExitCode":0},'
                '{"Service":"api","State":"running","Health":"healthy"},'
                '{"Service":"worker","State":"running","Health":"healthy"}]'
            )

        return Result()

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "_probe_runtime", lambda *_args, **_kwargs: True)

    result = bootstrap._install_docker_runtime(tmp_path, start=True)

    assert result.status == "complete"
    assert calls == [
        ["docker", "compose", "up", "-d", "--build"],
        [
            "docker",
            "compose",
            "ps",
            "--all",
            "--format",
            "json",
            "migrate",
            "api",
            "worker",
        ],
        [
            "docker",
            "compose",
            "ps",
            "--all",
            "--format",
            "json",
            "migrate",
            "api",
            "worker",
        ],
    ]


def test_docker_install_rechecks_worker_after_protected_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    status_calls = 0

    def run(command: list[str], **_kwargs: object) -> object:
        nonlocal status_calls

        class Result:
            returncode = 0
            stdout = ""

        result = Result()
        if "ps" in command:
            status_calls += 1
            worker = (
                {"Service": "worker", "State": "running", "Health": "healthy"}
                if status_calls == 1
                else {"Service": "worker", "State": "restarting", "Health": "unhealthy"}
            )
            result.stdout = json.dumps(
                [
                    {"Service": "migrate", "State": "exited", "ExitCode": 0},
                    {"Service": "api", "State": "running", "Health": "healthy"},
                    worker,
                ]
            )
        return result

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "_probe_runtime", lambda *_args, **_kwargs: True)

    with pytest.raises(RuntimeError, match="worker failed before becoming healthy"):
        bootstrap._install_docker_runtime(tmp_path, start=True)

    assert status_calls == 2


@pytest.mark.parametrize(
    "worker",
    [
        {"Service": "worker", "State": "restarting", "Health": "unhealthy"},
        {"Service": "worker", "State": "exited", "ExitCode": 1},
        {"Service": "worker", "State": "running", "Health": "unhealthy"},
    ],
)
def test_docker_install_rejects_worker_crash_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, worker: dict[str, object]
) -> None:
    (tmp_path / "docker-compose.yml").touch()

    def run(command: list[str], **_kwargs: object) -> object:
        class Result:
            returncode = 0
            stdout = json.dumps(
                [
                    {"Service": "migrate", "State": "exited", "ExitCode": 0},
                    {"Service": "api", "State": "running", "Health": "healthy"},
                    worker,
                ]
            )

        return Result()

    monkeypatch.setattr(bootstrap.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="worker failed before becoming healthy"):
        bootstrap._install_docker_runtime(tmp_path, start=True)


@pytest.mark.parametrize(
    ("migration", "api", "message"),
    [
        (
            {"Service": "migrate", "State": "exited", "ExitCode": 1},
            {"Service": "api", "State": "created"},
            "migration service failed",
        ),
        (
            {"Service": "migrate", "State": "exited", "ExitCode": 0},
            {"Service": "api", "State": "running", "Health": "unhealthy"},
            "api failed before becoming healthy",
        ),
    ],
)
def test_docker_install_rejects_migration_or_api_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    migration: dict[str, object],
    api: dict[str, object],
    message: str,
) -> None:
    (tmp_path / "docker-compose.yml").touch()

    def run(command: list[str], **_kwargs: object) -> object:
        class Result:
            returncode = 0
            stdout = json.dumps(
                [
                    migration,
                    api,
                    {"Service": "worker", "State": "running", "Health": "healthy"},
                ]
            )

        return Result()

    monkeypatch.setattr(bootstrap.subprocess, "run", run)

    with pytest.raises(RuntimeError, match=message):
        bootstrap._install_docker_runtime(tmp_path, start=True)


def test_docker_install_requires_protected_api_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "docker-compose.yml").touch()

    def run(command: list[str], **_kwargs: object) -> object:
        class Result:
            returncode = 0
            stdout = json.dumps(
                [
                    {"Service": "migrate", "State": "exited", "ExitCode": 0},
                    {"Service": "api", "State": "running", "Health": "healthy"},
                    {"Service": "worker", "State": "running", "Health": "healthy"},
                ]
            )

        return Result()

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "_probe_runtime", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="protected readiness check failed"):
        bootstrap._install_docker_runtime(tmp_path, start=True)


def test_compose_status_parser_accepts_newline_delimited_json() -> None:
    rows = bootstrap._compose_status_rows(
        '{"Service":"api","State":"running"}\n{"Service":"worker","State":"running"}\n'
    )

    assert set(rows) == {"api", "worker"}


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
