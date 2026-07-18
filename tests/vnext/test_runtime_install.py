from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest


def _load_bootstrap_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "runtime_bootstrap.py"
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
    assert "OPENBILICLAW_LITELLM_ADMIN_URL" not in values
    assert len(values["OPENBILICLAW_ACCESS_TOKEN"]) >= 48
    assert len(values["OPENBILICLAW_SECRET_KEY"]) == 64
    assert len(values["OPENBILICLAW_SESSION_SECRET"]) >= 48
    assert "OPENBILICLAW_WEB_PASSWORD_HASH" not in values
    assert "OPENBILICLAW_EXTENSION_ACCESS_KEYS" not in values
    assert "obc_ext_" not in env_path.read_text(encoding="utf-8")
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


def test_source_admin_url_is_persisted_only_when_explicitly_supplied(tmp_path: Path) -> None:
    values = bootstrap.ensure_local_runtime_environment(
        tmp_path,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="proxy-secret",
        litellm_admin_url="https://admin.example/proxy/ui",
    )

    assert values["OPENBILICLAW_LITELLM_ADMIN_URL"] == ("https://admin.example/proxy/ui")


def test_docker_admin_url_preserves_existing_value_unless_explicitly_replaced(
    tmp_path: Path,
) -> None:
    first = bootstrap.ensure_docker_infrastructure_secrets(
        tmp_path,
        litellm_admin_url="https://admin.example/custom/ui",
    )
    rerun = bootstrap.ensure_docker_infrastructure_secrets(tmp_path)
    replaced = bootstrap.ensure_docker_infrastructure_secrets(
        tmp_path,
        litellm_admin_url="https://new-admin.example/ui",
    )

    assert first["OPENBILICLAW_LITELLM_ADMIN_URL"] == "https://admin.example/custom/ui"
    assert rerun["OPENBILICLAW_LITELLM_ADMIN_URL"] == "https://admin.example/custom/ui"
    assert replaced["OPENBILICLAW_LITELLM_ADMIN_URL"] == "https://new-admin.example/ui"


def test_docker_cli_forwards_explicit_admin_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def install(project_dir: Path, **kwargs: object) -> object:
        captured["project_dir"] = project_dir
        captured.update(kwargs)
        return bootstrap.InstallResult("prepared", "docker", "http://127.0.0.1:8420")

    monkeypatch.setattr(bootstrap, "_install_docker_runtime", install)
    args = bootstrap.build_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "docker",
            "--skip-start",
            "--litellm-admin-url",
            "https://admin.example/custom/ui",
        ]
    )

    bootstrap.run(args)

    assert captured["litellm_admin_url"] == "https://admin.example/custom/ui"


@pytest.mark.parametrize(
    "admin_url",
    (
        "ftp://admin.example/ui",
        "https://user:secret@admin.example/ui",
        "https://admin.example/ui?token=secret",
        "https://admin.example/ui#fragment",
    ),
)
def test_source_admin_url_rejects_unsafe_or_credential_bearing_values(
    tmp_path: Path, admin_url: str
) -> None:
    with pytest.raises(ValueError, match="credential-free HTTP"):
        bootstrap.ensure_local_runtime_environment(
            tmp_path,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            litellm_admin_url=admin_url,
        )


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
    (copied / ".env").chmod(0o600)

    rebound = bootstrap.ensure_local_runtime_environment(
        copied,
        litellm_base_url="https://ignored.example/v1",
        litellm_api_key="ignored-secret",
    )

    assert rebound["OPENBILICLAW_SECRET_KEY"] == first["OPENBILICLAW_SECRET_KEY"]
    assert rebound["OPENBILICLAW_ACCESS_TOKEN"] == first["OPENBILICLAW_ACCESS_TOKEN"]
    assert rebound["OPENBILICLAW_SESSION_SECRET"] == first["OPENBILICLAW_SESSION_SECRET"]
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode contract")
def test_local_environment_rejects_public_runtime_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DO_NOT_TRUST=1\n", encoding="utf-8")
    env_path.chmod(0o644)

    with pytest.raises(RuntimeError, match="mode 0600"):
        bootstrap.ensure_local_runtime_environment(
            tmp_path,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
        )
    assert env_path.read_text(encoding="utf-8") == "DO_NOT_TRUST=1\n"


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
    events = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS:"))
        for line in rendered.splitlines()
        if line.startswith("BOOTSTRAP_STATUS:")
    ]
    credential_events = [event for event in events if event["message"] == "first_run_access"]
    assert len(credential_events) == 1
    credentials = credential_events[0]["details"]
    assert len(credentials["web_password"]) >= 20
    assert credentials["extension_access_key"].startswith("obc_ext_")
    persisted = (tmp_path / ".env").read_text(encoding="utf-8")
    assert credentials["web_password"] not in persisted
    assert credentials["extension_access_key"] not in persisted


def test_local_install_rerun_preserves_access_credentials_without_reexposing_plaintext(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def install() -> None:
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            start=False,
            run_command=lambda *_args, **_kwargs: None,
        )

    install()
    first_values = _read_env(tmp_path / ".env")
    first_output = capsys.readouterr().out
    install()
    second_values = _read_env(tmp_path / ".env")
    second_output = capsys.readouterr().out

    assert (
        second_values["OPENBILICLAW_WEB_PASSWORD_HASH"]
        == first_values["OPENBILICLAW_WEB_PASSWORD_HASH"]
    )
    assert (
        second_values["OPENBILICLAW_EXTENSION_ACCESS_KEYS"]
        == first_values["OPENBILICLAW_EXTENSION_ACCESS_KEYS"]
    )
    assert first_output.count('"message": "first_run_access"') == 1
    assert '"message": "first_run_access"' not in second_output


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


def test_failed_local_install_never_persists_or_discloses_staged_access(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(RuntimeError, match="migration failed"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            start=False,
            run_command=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("migration failed")
            ),
        )

    values = _read_env(tmp_path / ".env")
    assert "OPENBILICLAW_WEB_PASSWORD_HASH" not in values
    assert "OPENBILICLAW_EXTENSION_ACCESS_KEYS" not in values
    assert "first_run_access" not in capsys.readouterr().out


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
    values = _read_env(tmp_path / ".env")
    assert len(values["OPENBILICLAW_SESSION_SECRET"]) >= 48
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
    values = _read_env(tmp_path / ".env")
    assert len(values["OPENBILICLAW_SESSION_SECRET"]) >= 48
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


def test_failed_docker_install_never_persists_or_discloses_staged_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "docker-compose.yml").touch()

    def fail(*_args: object, **_kwargs: object) -> object:
        raise bootstrap.subprocess.CalledProcessError(1, "docker compose up")

    monkeypatch.setattr(bootstrap.subprocess, "run", fail)

    with pytest.raises(bootstrap.subprocess.CalledProcessError):
        bootstrap._install_docker_runtime(tmp_path, start=True)

    values = _read_env(tmp_path / ".env")
    assert "OPENBILICLAW_WEB_PASSWORD_HASH" not in values
    assert "OPENBILICLAW_EXTENSION_ACCESS_KEYS" not in values
    assert "first_run_access" not in capsys.readouterr().out


def test_concurrent_docker_installs_serialize_stage_start_commit_and_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    first_started = Event()
    release_first = Event()
    second_staged = Event()
    stage_lock = Lock()
    stage_count = 0
    original_stage = bootstrap._stage_runtime_access

    def stage(values, *, rotate_access=False):  # type: ignore[no-untyped-def]
        nonlocal stage_count
        with stage_lock:
            stage_count += 1
            if stage_count == 2:
                second_staged.set()
        return original_stage(values, rotate_access=rotate_access)

    def run_command(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        if not first_started.is_set():
            first_started.set()
            assert release_first.wait(5)
        return bootstrap.subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(bootstrap, "_stage_runtime_access", stage)
    monkeypatch.setattr(bootstrap.subprocess, "run", run_command)
    monkeypatch.setattr(bootstrap, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_emit_first_run_access", lambda *_args: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(bootstrap._install_docker_runtime, tmp_path, start=False)
        assert first_started.wait(5)
        second = executor.submit(bootstrap._install_docker_runtime, tmp_path, start=False)
        assert second_staged.wait(0.25) is False
        release_first.set()
        assert first.result(timeout=5).status == "prepared"
        assert second.result(timeout=5).status == "prepared"

    assert stage_count == 2


def test_windows_private_acl_uses_current_sid_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    modes: list[str] = []
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: "powershell.exe")

    def successful(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        modes.append(kwargs["env"]["OPENBILICLAW_PRIVATE_ACL_MODE"])
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["env"]["OPENBILICLAW_PRIVATE_ACL_MODE"] in {"apply", "verify"}
        assert kwargs["env"]["OPENBILICLAW_PRIVATE_ACL_TARGET"] == str(tmp_path / ".env")
        return bootstrap.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", successful)
    bootstrap._apply_windows_private_acl(tmp_path / ".env")
    bootstrap._verify_windows_private_acl(tmp_path / ".env")

    assert calls[0] == calls[1]
    assert modes == ["apply", "verify"]
    script = next(argument for argument in calls[0] if "WindowsIdentity" in argument)
    assert "SetAccessRuleProtection($true, $false)" in script

    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *args, **kwargs: bootstrap.subprocess.CompletedProcess(
            args[0], 19, stdout="", stderr="unsafe ACL"
        ),
    )
    with pytest.raises(RuntimeError, match="private Windows ACL"):
        bootstrap._verify_windows_private_acl(tmp_path / ".env")


def test_explicit_access_recovery_rotates_and_discloses_only_after_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def install(*, rotate_access: bool = False) -> None:
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            start=False,
            rotate_access=rotate_access,
            run_command=lambda *_args, **_kwargs: None,
        )

    install()
    first = _read_env(tmp_path / ".env")
    first_output = capsys.readouterr().out
    install(rotate_access=True)
    second = _read_env(tmp_path / ".env")
    second_output = capsys.readouterr().out

    assert first["OPENBILICLAW_WEB_PASSWORD_HASH"] != second["OPENBILICLAW_WEB_PASSWORD_HASH"]
    assert (
        first["OPENBILICLAW_EXTENSION_ACCESS_KEYS"] != second["OPENBILICLAW_EXTENSION_ACCESS_KEYS"]
    )
    assert first_output.count('"message": "first_run_access"') == 1
    assert second_output.count('"message": "first_run_access"') == 1


def test_local_completion_url_honors_custom_bind_host(tmp_path: Path) -> None:
    result = bootstrap.install_local_runtime(
        tmp_path,
        host="192.0.2.10",
        port=9450,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="proxy-secret",
        install_dependencies=False,
        start=False,
        run_command=lambda *_args, **_kwargs: None,
    )

    assert result.health_url == "http://192.0.2.10:9450/api/v1/system/readiness"


def test_docker_install_honors_public_host_and_port_in_compose_and_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "docker-compose.yml").touch()
    monkeypatch.setenv("LITELLM_PORT", "4500")
    probes: list[tuple[str, int]] = []

    class Result:
        returncode = 0
        stdout = (
            '[{"Service":"migrate","State":"exited","ExitCode":0},'
            '{"Service":"api","State":"running","Health":"healthy"},'
            '{"Service":"worker","State":"running","Health":"healthy"}]'
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(
        bootstrap,
        "_probe_runtime",
        lambda host, port, *_args, **_kwargs: probes.append((host, port)) or True,
    )

    result = bootstrap._install_docker_runtime(tmp_path, start=True, host="127.0.0.1", port=9450)

    values = _read_env(tmp_path / ".env")
    assert values["OPENBILICLAW_API_HOST"] == "127.0.0.1"
    assert values["OPENBILICLAW_API_PORT"] == "9450"
    assert values["OPENBILICLAW_LITELLM_ADMIN_URL"] == "http://127.0.0.1:4500/ui"
    assert result.health_url == "http://127.0.0.1:9450/api/v1/system/readiness"
    assert probes == [("127.0.0.1", 9450)]


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
