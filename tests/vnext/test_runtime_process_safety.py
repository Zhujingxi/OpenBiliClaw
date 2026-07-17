from __future__ import annotations

import importlib.util
import json
import signal
import sys
from pathlib import Path

import pytest


def _load_bootstrap_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("vnext_process_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def _identity(pid: int, token: str) -> object:
    return bootstrap.ProcessIdentity(
        pid=pid,
        start_token=token,
        executable="/runtime/openbiliclaw",
        argv_fingerprint=f"argv-{token}",
    )


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        exit_code: int | None = None,
        terminate_exits: bool = True,
    ) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.terminate_exits = terminate_exits
        self.signals: list[str] = []
        self.waits: list[float] = []

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.signals.append("terminate")
        if self.terminate_exits:
            self.exit_code = -signal.SIGTERM

    def kill(self) -> None:
        self.signals.append("kill")
        self.exit_code = -signal.SIGKILL

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None:
            self.waits.append(timeout)
        if self.exit_code is None:
            raise bootstrap.subprocess.TimeoutExpired("fake", timeout)
        return self.exit_code


def _install(
    tmp_path: Path,
    *,
    start_process,
    state_writer=None,
) -> object:
    identities = {
        1001: _identity(1001, "api-start"),
        1002: _identity(1002, "worker-start"),
    }
    kwargs = {}
    if state_writer is not None:
        kwargs["state_writer"] = state_writer
    return bootstrap.install_local_runtime(
        tmp_path,
        host="127.0.0.1",
        port=8420,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="proxy-secret",
        install_dependencies=False,
        run_command=lambda *_args, **_kwargs: None,
        start_process=start_process,
        readiness_probe=lambda *_args, **_kwargs: True,
        queue_probe=lambda *_args, **_kwargs: True,
        identity_probe=lambda pid: identities.get(pid),
        **kwargs,
    )


def test_rerun_never_signals_reused_or_mismatched_pid(tmp_path: Path) -> None:
    expected = _identity(4321, "original-start")
    bootstrap._write_process_state(tmp_path, api=expected, worker=expected)
    signals: list[tuple[int, int]] = []

    bootstrap._stop_managed_processes(
        tmp_path,
        identity_probe=lambda _pid: _identity(4321, "reused-start"),
        signal_process=lambda pid, signum: signals.append((pid, signum)),
        wait_for_exit=lambda *_args, **_kwargs: True,
    )

    assert signals == []
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_rerun_signals_verified_identity_then_waits_before_escalating(tmp_path: Path) -> None:
    api = _identity(4321, "api-start")
    worker = _identity(4322, "worker-start")
    bootstrap._write_process_state(tmp_path, api=api, worker=worker)
    signals: list[tuple[int, int]] = []
    waits: list[tuple[int, float]] = []

    def wait_for_exit(identity: object, timeout: float) -> bool:
        waits.append((identity.pid, timeout))
        return identity.pid == 4322 or len([pid for pid, _ in waits if pid == 4321]) > 1

    bootstrap._stop_managed_processes(
        tmp_path,
        identity_probe=lambda pid: {4321: api, 4322: worker}[pid],
        signal_process=lambda pid, signum: signals.append((pid, signum)),
        wait_for_exit=wait_for_exit,
    )

    assert signals == [
        (4321, signal.SIGTERM),
        (4321, signal.SIGKILL),
        (4322, signal.SIGTERM),
    ]
    assert waits == [(4321, 5.0), (4321, 5.0), (4322, 5.0)]


def test_rerun_refuses_to_launch_when_verified_process_cannot_be_reaped(tmp_path: Path) -> None:
    api = _identity(4321, "api-start")
    bootstrap._write_process_state(tmp_path, api=api, worker=api)

    with pytest.raises(RuntimeError, match="managed processes did not stop"):
        bootstrap._stop_managed_processes(
            tmp_path,
            identity_probe=lambda _pid: api,
            signal_process=lambda *_args: None,
            wait_for_exit=lambda *_args: False,
        )

    assert (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_api_immediate_exit_prevents_worker_launch(tmp_path: Path) -> None:
    api = FakeProcess(1001, exit_code=3)
    launches = 0

    def start_process(*_args: object, **_kwargs: object) -> FakeProcess:
        nonlocal launches
        launches += 1
        return api

    with pytest.raises(RuntimeError, match="API exited before readiness"):
        _install(tmp_path, start_process=start_process)

    assert launches == 1
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_worker_immediate_exit_is_not_masked_by_stale_queue(tmp_path: Path) -> None:
    api = FakeProcess(1001)
    worker = FakeProcess(1002, exit_code=2)
    processes = iter((api, worker))

    with pytest.raises(RuntimeError, match="worker exited before readiness"):
        _install(tmp_path, start_process=lambda *_args, **_kwargs: next(processes))

    assert api.signals == ["terminate"]
    assert api.waits == [5.0]
    assert worker.waits == []
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_http_readiness_cannot_mask_worker_exit(tmp_path: Path) -> None:
    api = FakeProcess(1001)
    worker = FakeProcess(1002)
    processes = iter((api, worker))
    identities = {
        1001: _identity(1001, "api-start"),
        1002: _identity(1002, "worker-start"),
    }

    def readiness_probe(*_args: object, **_kwargs: object) -> bool:
        worker.exit_code = 9
        return True

    with pytest.raises(RuntimeError, match="worker exited before readiness"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=lambda *_args, **_kwargs: None,
            start_process=lambda *_args, **_kwargs: next(processes),
            readiness_probe=readiness_probe,
            queue_probe=lambda *_args, **_kwargs: True,
            identity_probe=lambda pid: identities.get(pid),
        )

    assert api.signals == ["terminate"]
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_state_write_failure_terminates_and_reaps_both_children(tmp_path: Path) -> None:
    api = FakeProcess(1001, terminate_exits=False)
    worker = FakeProcess(1002, terminate_exits=False)
    processes = iter((api, worker))

    def fail_state_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        _install(
            tmp_path,
            start_process=lambda *_args, **_kwargs: next(processes),
            state_writer=fail_state_write,
        )

    assert api.signals == ["terminate", "kill"]
    assert worker.signals == ["terminate", "kill"]
    assert api.waits == [5.0, 5.0]
    assert worker.waits == [5.0, 5.0]
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_partial_worker_launch_failure_reaps_api_without_state_leak(tmp_path: Path) -> None:
    api = FakeProcess(1001, terminate_exits=False)
    launches = 0

    def start_process(*_args: object, **_kwargs: object) -> FakeProcess:
        nonlocal launches
        launches += 1
        if launches == 2:
            raise OSError("worker launch failed")
        return api

    with pytest.raises(OSError, match="worker launch failed"):
        _install(tmp_path, start_process=start_process)

    assert api.signals == ["terminate", "kill"]
    assert api.waits == [5.0, 5.0]
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_process_state_contains_verified_identity_not_bare_pids(tmp_path: Path) -> None:
    processes = iter((FakeProcess(1001), FakeProcess(1002)))

    _install(tmp_path, start_process=lambda *_args, **_kwargs: next(processes))

    state = json.loads((tmp_path / bootstrap.PROCESS_STATE).read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert state["api"] == {
        "argv_fingerprint": "argv-api-start",
        "executable": "/runtime/openbiliclaw",
        "pid": 1001,
        "start_token": "api-start",
    }
    assert state["worker"]["start_token"] == "worker-start"


def test_current_process_identity_is_stable_and_fingerprinted() -> None:
    first = bootstrap._inspect_process(bootstrap.os.getpid())
    second = bootstrap._inspect_process(bootstrap.os.getpid())

    assert first is not None
    assert first == second
    assert first.pid == bootstrap.os.getpid()
    assert len(first.argv_fingerprint) == 64
