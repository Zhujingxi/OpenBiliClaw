from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest


def _load_bootstrap_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("vnext_serial_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.exit_code = -signal.SIGTERM

    def kill(self) -> None:
        self.exit_code = -signal.SIGKILL

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.exit_code is None:
            raise bootstrap.subprocess.TimeoutExpired("fake", 5.0)
        return self.exit_code


def _identity(pid: int):
    return bootstrap.ProcessIdentity(
        pid=pid,
        start_token=f"start-{pid}",
        executable="/runtime/openbiliclaw",
        argv_fingerprint=f"argv-{pid}",
    )


def _runtime_payload(ownership, *, api_pid: int = 1001, worker_pid: int = 1002):
    return {
        "version": 2,
        "project_root": ownership.project_root,
        "instance_id": ownership.instance_id,
        "generation": ownership.generation,
        "api": _identity(api_pid).to_dict(),
        "worker": _identity(worker_pid).to_dict(),
    }


def test_installer_identity_is_private_stable_and_canonical_root_bound(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        first = bootstrap._load_or_create_installation_state(tmp_path)
        second = bootstrap._load_or_create_installation_state(tmp_path)

    assert first == second
    assert first.project_root == str(tmp_path.resolve())
    assert str(UUID(first.instance_id)) == first.instance_id
    assert first.generation == 0
    identity_path = tmp_path / bootstrap.INSTALLATION_STATE
    if os.name != "nt":
        assert stat.S_IMODE(identity_path.stat().st_mode) == 0o600

    copied = tmp_path.parent / f"{tmp_path.name}-copied"
    shutil.copytree(tmp_path, copied)
    with (
        bootstrap._lifecycle_lock(copied, timeout=1.0),
        pytest.raises(RuntimeError, match="different project root"),
    ):
        bootstrap._load_or_create_installation_state(copied)


def test_moved_installation_state_is_refused(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.mkdir()
    with bootstrap._lifecycle_lock(original, timeout=1.0):
        bootstrap._load_or_create_installation_state(original)

    moved = tmp_path / "moved"
    original.rename(moved)

    with (
        bootstrap._lifecycle_lock(moved, timeout=1.0),
        pytest.raises(RuntimeError, match="different project root"),
    ):
        bootstrap._load_or_create_installation_state(moved)


@pytest.mark.parametrize("field", ("project_root", "instance_id", "generation"))
def test_stop_refuses_tampered_runtime_ownership(tmp_path: Path, field: str) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        installation = bootstrap._load_or_create_installation_state(tmp_path)
        ownership = bootstrap._advance_installation_generation(tmp_path, installation)
        payload = _runtime_payload(ownership)
        if field == "project_root":
            payload[field] = str(tmp_path.parent.resolve())
        elif field == "instance_id":
            payload[field] = "00000000-0000-4000-8000-000000000000"
        else:
            payload[field] = ownership.generation + 1
        bootstrap._atomic_write_private_file(
            tmp_path / bootstrap.PROCESS_STATE,
            json.dumps(payload, sort_keys=True) + "\n",
        )

        signals: list[tuple[int, int]] = []
        with pytest.raises(RuntimeError, match="ownership"):
            bootstrap._stop_managed_processes(
                tmp_path,
                installation=ownership,
                identity_probe=lambda pid: _identity(pid),
                signal_process=lambda pid, signum: signals.append((pid, signum)),
                wait_for_exit=lambda *_args: True,
            )

    assert signals == []
    assert (tmp_path / bootstrap.PROCESS_STATE).exists()


def test_cleanup_compare_and_swap_cannot_unlink_newer_runtime_state(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        installation = bootstrap._load_or_create_installation_state(tmp_path)
        old = bootstrap._advance_installation_generation(tmp_path, installation)
        newer_payload = _runtime_payload(
            bootstrap.InstallationState(
                project_root=old.project_root,
                instance_id=old.instance_id,
                generation=old.generation + 1,
            ),
            api_pid=2001,
            worker_pid=2002,
        )
        bootstrap._atomic_write_private_file(
            tmp_path / bootstrap.PROCESS_STATE,
            json.dumps(newer_payload, sort_keys=True) + "\n",
        )

        removed = bootstrap._remove_process_state_if_owned(tmp_path, old)

    assert removed is False
    assert json.loads((tmp_path / bootstrap.PROCESS_STATE).read_text()) == newer_payload


def test_concurrent_prepare_serializes_migrations(tmp_path: Path) -> None:
    first_migration_entered = threading.Event()
    second_invoked = threading.Event()
    release_first = threading.Event()
    timeline: list[str] = []
    timeline_lock = threading.Lock()
    migration_count = 0

    def run_command(command: list[str], **_kwargs: object) -> None:
        nonlocal migration_count
        assert command[-2:] == ["db", "migrate"]
        with timeline_lock:
            migration_count += 1
            number = migration_count
            timeline.append(f"start-{number}")
        if number == 1:
            first_migration_entered.set()
            assert second_invoked.wait(2.0)
            assert release_first.wait(2.0)
        with timeline_lock:
            timeline.append(f"end-{number}")

    def prepare(second: bool) -> object:
        if second:
            second_invoked.set()
        return bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            start=False,
            run_command=run_command,
            lifecycle_timeout=2.0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prepare, False)
        assert first_migration_entered.wait(2.0)
        second = pool.submit(prepare, True)
        assert second_invoked.wait(2.0)
        release_first.set()
        assert first.result(timeout=2.0).status == "prepared"
        assert second.result(timeout=2.0).status == "prepared"

    assert timeline == ["start-1", "end-1", "start-2", "end-2"]


def test_prepare_verifiably_stops_managed_pair_before_migration(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        installation = bootstrap._load_or_create_installation_state(tmp_path)
        ownership = bootstrap._advance_installation_generation(tmp_path, installation)
        bootstrap._write_process_state(
            tmp_path,
            ownership=ownership,
            api=_identity(1001),
            worker=_identity(1002),
        )

    live = {1001, 1002}
    timeline: list[str] = []

    def identity_probe(pid: int):
        return _identity(pid) if pid in live else None

    def signal_process(pid: int, _signum: int) -> None:
        timeline.append(f"stop-{pid}")
        live.discard(pid)

    def run_command(command: list[str], **_kwargs: object) -> None:
        assert command[-2:] == ["db", "migrate"]
        timeline.append("migrate")

    result = bootstrap.install_local_runtime(
        tmp_path,
        host="127.0.0.1",
        port=8420,
        litellm_base_url="https://models.example/v1",
        litellm_api_key="proxy-secret",
        install_dependencies=False,
        start=False,
        run_command=run_command,
        identity_probe=identity_probe,
        signal_process=signal_process,
        wait_for_exit=lambda identity, _timeout: identity_probe(identity.pid) is None,
    )

    assert result.status == "prepared"
    assert timeline == ["stop-1001", "stop-1002", "migrate"]
    assert not (tmp_path / bootstrap.PROCESS_STATE).exists()
    persisted = json.loads((tmp_path / bootstrap.INSTALLATION_STATE).read_text())
    assert persisted["generation"] == 1


def test_lifecycle_lock_wait_is_bounded(tmp_path: Path) -> None:
    held = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
            held.set()
            assert release.wait(2.0)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_lock)
        assert held.wait(2.0)
        with (
            pytest.raises(RuntimeError, match="timed out waiting for lifecycle lock"),
            bootstrap._lifecycle_lock(tmp_path, timeout=0.01),
        ):
            pytest.fail("contended lifecycle lock was acquired")
        release.set()
        holder.result(timeout=2.0)


def test_concurrent_start_leaves_only_newest_pair_live_and_tracked(tmp_path: Path) -> None:
    first_readiness_entered = threading.Event()
    second_invoked = threading.Event()
    release_first = threading.Event()
    processes: dict[int, FakeProcess] = {}
    next_pid = 1000
    launch_lock = threading.Lock()
    migration_active = 0
    max_migration_active = 0

    def run_command(command: list[str], **_kwargs: object) -> None:
        nonlocal migration_active, max_migration_active
        if command[-1] == "doctor":
            return
        assert command[-2:] == ["db", "migrate"]
        with launch_lock:
            migration_active += 1
            max_migration_active = max(max_migration_active, migration_active)
        with launch_lock:
            migration_active -= 1

    def start_process(*_args: object, **_kwargs: object) -> FakeProcess:
        nonlocal next_pid
        with launch_lock:
            next_pid += 1
            process = FakeProcess(next_pid)
            processes[next_pid] = process
            return process

    readiness_calls = 0

    def readiness_probe(*_args: object, **_kwargs: object) -> bool:
        nonlocal readiness_calls
        with launch_lock:
            readiness_calls += 1
            number = readiness_calls
        if number == 1:
            first_readiness_entered.set()
            assert second_invoked.wait(2.0)
            assert release_first.wait(2.0)
        return True

    def identity_probe(pid: int):
        process = processes.get(pid)
        return _identity(pid) if process is not None and process.poll() is None else None

    def signal_process(pid: int, signum: int) -> None:
        process = processes[pid]
        if signum == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    def install(second: bool) -> object:
        if second:
            second_invoked.set()
        return bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=run_command,
            start_process=start_process,
            readiness_probe=readiness_probe,
            queue_probe=lambda *_args, **_kwargs: True,
            identity_probe=identity_probe,
            signal_process=signal_process,
            wait_for_exit=lambda identity, _timeout: identity_probe(identity.pid) is None,
            lifecycle_timeout=2.0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(install, False)
        assert first_readiness_entered.wait(2.0)
        second = pool.submit(install, True)
        assert second_invoked.wait(2.0)
        release_first.set()
        first_result = first.result(timeout=2.0)
        second_result = second.result(timeout=2.0)

    state = json.loads((tmp_path / bootstrap.PROCESS_STATE).read_text())
    assert max_migration_active == 1
    assert first_result.api_pid is not None and first_result.worker_pid is not None
    assert processes[first_result.api_pid].poll() is not None
    assert processes[first_result.worker_pid].poll() is not None
    assert second_result.api_pid is not None and second_result.worker_pid is not None
    assert processes[second_result.api_pid].poll() is None
    assert processes[second_result.worker_pid].poll() is None
    assert state["api"]["pid"] == second_result.api_pid
    assert state["worker"]["pid"] == second_result.worker_pid
    assert state["generation"] == 2


def test_failed_old_invocation_cleanup_preserves_newer_state(tmp_path: Path) -> None:
    processes = iter((FakeProcess(1001), FakeProcess(1002)))

    def publish_newer_then_fail(project_dir: Path, *, ownership, **_kwargs: object) -> None:
        payload = _runtime_payload(
            bootstrap.InstallationState(
                project_root=ownership.project_root,
                instance_id=ownership.instance_id,
                generation=ownership.generation + 1,
            ),
            api_pid=2001,
            worker_pid=2002,
        )
        bootstrap._atomic_write_private_file(
            project_dir / bootstrap.PROCESS_STATE,
            json.dumps(payload, sort_keys=True) + "\n",
        )
        raise OSError("publication interrupted")

    with pytest.raises(OSError, match="publication interrupted"):
        bootstrap.install_local_runtime(
            tmp_path,
            host="127.0.0.1",
            port=8420,
            litellm_base_url="https://models.example/v1",
            litellm_api_key="proxy-secret",
            install_dependencies=False,
            run_command=lambda *_args, **_kwargs: None,
            start_process=lambda *_args, **_kwargs: next(processes),
            readiness_probe=lambda *_args, **_kwargs: True,
            queue_probe=lambda *_args, **_kwargs: True,
            identity_probe=lambda pid: _identity(pid),
            state_writer=publish_newer_then_fail,
        )

    state = json.loads((tmp_path / bootstrap.PROCESS_STATE).read_text())
    assert state["generation"] == 2
    assert state["api"]["pid"] == 2001
