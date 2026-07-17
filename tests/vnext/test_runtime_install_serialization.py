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
from types import SimpleNamespace
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
        pytest.raises(RuntimeError, match="lifecycle lock identity changed"),
        bootstrap._lifecycle_lock(copied, timeout=1.0),
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
        pytest.raises(RuntimeError, match="lifecycle lock identity changed"),
        bootstrap._lifecycle_lock(moved, timeout=1.0),
    ):
        bootstrap._load_or_create_installation_state(moved)


def test_lifecycle_lock_replacement_cannot_create_a_second_holder(tmp_path: Path) -> None:
    first_entered = threading.Event()
    replaced = threading.Event()
    release_first = threading.Event()
    second_entered = False

    def hold_first() -> None:
        with (
            pytest.raises(RuntimeError, match="lock identity changed"),
            bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
        ):
            first_entered.set()
            lock_path = tmp_path / bootstrap.LIFECYCLE_LOCK
            displaced = lock_path.with_suffix(".displaced")
            lock_path.rename(displaced)
            lock_path.write_bytes(displaced.read_bytes())
            replaced.set()
            assert release_first.wait(2.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold_first)
        assert first_entered.wait(2.0)
        assert replaced.wait(2.0)
        with (
            pytest.raises(RuntimeError, match="lock identity"),
            bootstrap._lifecycle_lock(tmp_path, timeout=0.1),
        ):
            second_entered = True
        release_first.set()
        first.result(timeout=2.0)

    assert not second_entered


def test_absent_active_lock_path_cannot_create_a_second_holder(tmp_path: Path) -> None:
    first_entered = threading.Event()
    removed = threading.Event()
    release_first = threading.Event()
    second_entered = False

    def hold_first() -> None:
        with (
            pytest.raises(RuntimeError, match="lock identity changed"),
            bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
        ):
            bootstrap._load_or_create_installation_state(tmp_path)
            first_entered.set()
            lock_path = tmp_path / bootstrap.LIFECYCLE_LOCK
            lock_path.rename(lock_path.with_suffix(".displaced"))
            removed.set()
            assert release_first.wait(2.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold_first)
        assert first_entered.wait(2.0)
        assert removed.wait(2.0)
        with (
            pytest.raises(RuntimeError, match="lock identity"),
            bootstrap._lifecycle_lock(tmp_path, timeout=0.1),
        ):
            second_entered = True
        release_first.set()
        first.result(timeout=2.0)

    assert not second_entered


def test_direct_path_lifecycle_branch_avoids_dir_fd_operations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_open = bootstrap.os.open
    observed_dir_fds: list[int | None] = []

    def record_open(*args: object, **kwargs: object) -> int:
        observed_dir_fds.append(kwargs.get("dir_fd"))  # type: ignore[arg-type]
        return original_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(bootstrap, "_lifecycle_uses_dir_fd", lambda: False, raising=False)
    monkeypatch.setattr(bootstrap.os, "open", record_open)

    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    assert all(descriptor is None for descriptor in observed_dir_fds)


def test_project_root_rejects_windows_junction_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_path_is_link_or_junction",
        lambda path: path == tmp_path,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="symlinked project path"):
        bootstrap._canonical_project_root(tmp_path)


def test_python311_windows_reparse_metadata_is_rejected_without_is_junction() -> None:
    class Python311ReparsePath:
        def is_symlink(self) -> bool:
            return False

        def lstat(self) -> object:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=0x400,
            )

    assert bootstrap._path_is_link_or_junction(Python311ReparsePath())


@pytest.mark.parametrize("direct_path", (False, True), ids=("dir-fd", "direct-path"))
def test_simultaneous_first_install_waits_through_anchor_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, direct_path: bool
) -> None:
    before_publication = threading.Event()
    release_publication = threading.Event()
    second_started = threading.Event()
    timeline: list[str] = []
    original = bootstrap._persist_initial_installation
    if direct_path:
        monkeypatch.setattr(bootstrap, "_lifecycle_uses_dir_fd", lambda: False)

    def delayed_publication(root: Path, anchor: object, installation: object) -> None:
        before_publication.set()
        assert release_publication.wait(2.0)
        original(root, anchor, installation)

    def enter(name: str) -> None:
        if name == "second":
            second_started.set()
        with bootstrap._lifecycle_lock(tmp_path, timeout=2.0):
            timeline.append(f"{name}-entered")

    monkeypatch.setattr(bootstrap, "_persist_initial_installation", delayed_publication)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(enter, "first")
        assert before_publication.wait(2.0)
        second = pool.submit(enter, "second")
        assert second_started.wait(2.0)
        assert not second.done()
        release_publication.set()
        first.result(timeout=2.0)
        second.result(timeout=2.0)

    assert timeline == ["first-entered", "second-entered"]


@pytest.mark.parametrize("direct_path", (False, True), ids=("dir-fd", "direct-path"))
def test_first_install_recovers_crash_after_anchor_write_before_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, direct_path: bool
) -> None:
    original = bootstrap._persist_initial_installation
    crashed = False
    if direct_path:
        monkeypatch.setattr(bootstrap, "_lifecycle_uses_dir_fd", lambda: False)

    def crash_once(root: Path, anchor: object, installation: object) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("simulated initialization crash")
        original(root, anchor, installation)

    monkeypatch.setattr(bootstrap, "_persist_initial_installation", crash_once)
    with (
        pytest.raises(RuntimeError, match="simulated initialization crash"),
        bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
    ):
        pytest.fail("crashed initialization entered lifecycle work")

    anchor_path = tmp_path / bootstrap.LIFECYCLE_LOCK
    orphan = anchor_path.stat()
    orphan_payload = json.loads(anchor_path.read_text())
    anchor_unlinked = False
    original_unlink = bootstrap.os.unlink

    def observed_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal anchor_unlinked
        if Path(os.fsdecode(path)).name == bootstrap.LIFECYCLE_LOCK.name:
            anchor_unlinked = True
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(bootstrap.os, "unlink", observed_unlink)
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)
    recovered = anchor_path.stat()
    recovered_payload = json.loads(anchor_path.read_text())

    assert not anchor_unlinked
    assert (orphan.st_dev, orphan.st_ino) == (recovered.st_dev, recovered.st_ino)
    assert orphan_payload["anchor_id"] == recovered_payload["anchor_id"]


def test_initial_installer_record_publication_never_replaces_a_racing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = tmp_path / bootstrap.INSTALLATION_STATE
    collision_attempted = False
    original_link = bootstrap.os.link

    def collide_before_link(source: object, destination: object, **kwargs: object) -> None:
        nonlocal collision_attempted
        collision_attempted = True
        state_path.write_text("attacker-owned\n", encoding="utf-8")
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(bootstrap.os, "link", collide_before_link)

    with (
        pytest.raises(RuntimeError, match="ownership metadata"),
        bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
    ):
        pytest.fail("racing installer record was overwritten")

    assert collision_attempted
    assert state_path.read_text(encoding="utf-8") == "attacker-owned\n"


def test_unbound_hardlinked_anchor_is_never_sanitized_or_adopted(tmp_path: Path) -> None:
    anchor_path = tmp_path / bootstrap.LIFECYCLE_LOCK
    anchor_path.parent.mkdir(parents=True)
    anchor_path.write_text("untrusted\n", encoding="utf-8")
    anchor_path.chmod(0o600)
    os.link(anchor_path, anchor_path.with_suffix(".external-link"))

    with (
        pytest.raises(RuntimeError, match="lock identity changed"),
        bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
    ):
        pytest.fail("hardlinked unbound anchor entered lifecycle work")

    assert anchor_path.read_text(encoding="utf-8") == "untrusted\n"


@pytest.mark.parametrize("field", ("instance_id", "generation"))
def test_holder_and_waiter_reject_full_installation_state_tamper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    holder_entered = threading.Event()
    release_holder = threading.Event()
    waiter_blocked = threading.Event()
    original_acquire = bootstrap._acquire_lock_before
    work: list[str] = []

    def observed_acquire(descriptor: int, *, path: Path, timeout: float | None) -> None:
        if threading.current_thread().name == "state-waiter":
            waiter_blocked.set()
        original_acquire(descriptor, path=path, timeout=timeout)

    def hold() -> None:
        with bootstrap._lifecycle_lock(tmp_path, timeout=2.0):
            holder_entered.set()
            assert release_holder.wait(2.0)

    def wait() -> None:
        threading.current_thread().name = "state-waiter"
        with bootstrap._lifecycle_lock(tmp_path, timeout=2.0):
            work.append("waiter-entered")

    monkeypatch.setattr(bootstrap, "_acquire_lock_before", observed_acquire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold)
        assert holder_entered.wait(2.0)
        waiter = pool.submit(wait)
        assert waiter_blocked.wait(2.0)
        state_path = tmp_path / bootstrap.INSTALLATION_STATE
        payload = json.loads(state_path.read_text())
        if field == "instance_id":
            payload[field] = "00000000-0000-4000-8000-000000000000"
        else:
            payload[field] += 99
        bootstrap._atomic_write_private_file(
            state_path,
            json.dumps(payload, sort_keys=True) + "\n",
        )
        release_holder.set()
        with pytest.raises(RuntimeError, match="lock identity changed"):
            holder.result(timeout=2.0)
        with pytest.raises(RuntimeError, match="lock identity changed"):
            waiter.result(timeout=2.0)

    assert work == []


def test_legitimate_generation_advance_remains_bound(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        current = bootstrap._load_or_create_installation_state(tmp_path)
        advanced = bootstrap._advance_installation_generation(tmp_path, current)

    assert advanced.instance_id == current.instance_id
    assert advanced.generation == current.generation + 1


def test_rebound_anchor_retry_cannot_restart_lifecycle_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    acquire_calls = 0
    original_acquire = bootstrap._acquire_lock_before

    def count_acquire(descriptor: int, *, path: Path, timeout: float | None) -> None:
        nonlocal acquire_calls
        acquire_calls += 1
        original_acquire(descriptor, path=path, timeout=timeout)

    def fail_validation(**_kwargs: object) -> None:
        raise RuntimeError("lifecycle lock identity changed")

    monkeypatch.setattr(bootstrap, "_acquire_lock_before", count_acquire)
    monkeypatch.setattr(bootstrap, "_require_lifecycle_anchor", fail_validation)

    with (
        pytest.raises(RuntimeError, match="lock identity changed"),
        bootstrap._lifecycle_lock_dir_fd(tmp_path.resolve(), None, timeout=0.01),
    ):
        pytest.fail("unbound retry entered lifecycle work")

    assert acquire_calls == 1


def test_windows_unbound_recovery_never_calls_unix_fchmod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    anchor = tmp_path / "orphan.lock"
    anchor.write_text("orphan\n", encoding="utf-8")
    anchor.chmod(0o600)
    descriptor = os.open(anchor, os.O_RDWR)
    fchmod_called = False

    def reject_fchmod(_descriptor: int, _mode: int) -> None:
        nonlocal fchmod_called
        fchmod_called = True
        raise AssertionError("Unix fchmod reached native Windows recovery")

    monkeypatch.setattr(bootstrap.os, "name", "nt")
    monkeypatch.setattr(bootstrap.os, "fchmod", reject_fchmod)
    try:
        bootstrap._sanitize_unbound_anchor_direct(anchor, descriptor)
    finally:
        os.close(descriptor)

    assert not fchmod_called


def test_windows_runtime_log_rejects_reparse_swap_with_native_handle_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, int]] = []

    def create(_path: Path, **kwargs: int) -> int:
        calls.append(kwargs)
        return len(calls)

    monkeypatch.setattr(bootstrap, "_windows_create_file", create)
    monkeypatch.setattr(
        bootstrap,
        "_windows_file_metadata",
        lambda handle: (
            (bootstrap._WIN_ATTRIBUTE_DIRECTORY if handle == 1 else 0)
            | (bootstrap._WIN_ATTRIBUTE_REPARSE_POINT if handle == 2 else 0),
            1,
        ),
    )
    monkeypatch.setattr(bootstrap, "_windows_close_handle", lambda _handle: None)
    monkeypatch.setattr(
        bootstrap, "_windows_handle_to_fd", lambda _handle: pytest.fail("reparse opened")
    )

    with pytest.raises(RuntimeError, match="private regular file"):
        bootstrap._open_windows_runtime_log(tmp_path / "logs", tmp_path / "logs/api.log")

    assert all(call["share"] & 0x4 == 0 for call in calls)
    assert calls[0]["flags"] & bootstrap._WIN_FLAG_OPEN_REPARSE_POINT
    assert calls[0]["flags"] & bootstrap._WIN_FLAG_BACKUP_SEMANTICS
    assert calls[1]["flags"] == bootstrap._WIN_FLAG_OPEN_REPARSE_POINT


def test_windows_file_information_layout_matches_native_abi() -> None:
    assert bootstrap.ctypes.sizeof(bootstrap._WindowsFileTime) == 8
    assert bootstrap.ctypes.sizeof(bootstrap._WindowsByHandleFileInformation) == 52
    assert bootstrap._WindowsByHandleFileInformation.link_count.offset == 40


def test_generation_guard_first_crash_recovers_exactly_one_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        current = bootstrap._load_or_create_installation_state(tmp_path)
        original_write = bootstrap._atomic_write_private_file
        failed = False

        def crash_record(path: Path, content: str) -> None:
            nonlocal failed
            if path == tmp_path / bootstrap.INSTALLATION_STATE and not failed:
                failed = True
                raise OSError("crash after guard lease")
            original_write(path, content)

        monkeypatch.setattr(bootstrap, "_atomic_write_private_file", crash_record)
        with pytest.raises(OSError, match="crash after guard lease"):
            bootstrap._advance_installation_generation(tmp_path, current)

    monkeypatch.setattr(bootstrap, "_atomic_write_private_file", original_write)
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        recovered = bootstrap._load_or_create_installation_state(tmp_path)
    assert recovered.generation == current.generation + 1


def test_guard_append_trims_incomplete_tail_and_preserves_last_complete_lease(
    tmp_path: Path,
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        current = bootstrap._load_or_create_installation_state(tmp_path)

    guard_path = tmp_path / bootstrap.ROOT_LIFECYCLE_GUARD
    with guard_path.open("ab") as stream:
        stream.write(b'{"generation":')
        stream.flush()
        os.fsync(stream.fileno())

    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        recovered = bootstrap._load_or_create_installation_state(tmp_path)
        advanced = bootstrap._advance_installation_generation(tmp_path, recovered)

    assert recovered == current
    assert advanced.generation == current.generation + 1
    assert guard_path.read_bytes().endswith(b"\n")


def test_guard_history_rejects_corrupt_earlier_complete_record(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        current = bootstrap._load_or_create_installation_state(tmp_path)
        bootstrap._advance_installation_generation(tmp_path, current)
    guard = tmp_path / bootstrap.ROOT_LIFECYCLE_GUARD
    records = guard.read_bytes().splitlines(keepends=True)
    records[0] = b'{"corrupt":true}\n'
    guard.write_bytes(b"".join(records))
    with (
        pytest.raises(RuntimeError, match="guard metadata|guard history"),
        bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
    ):
        pytest.fail("corrupt earlier guard record was ignored")


def test_guard_history_commits_each_generation_with_duplicate_records(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        current = bootstrap._load_or_create_installation_state(tmp_path)
        bootstrap._advance_installation_generation(tmp_path, current)
    guard = tmp_path / bootstrap.ROOT_LIFECYCLE_GUARD
    generations = [json.loads(line)["generation"] for line in guard.read_text().splitlines()]
    assert generations == [0, 0, 1, 1]


def test_atomic_write_private_file_holds_source_and_never_path_chmods(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    chmod_paths: list[Path] = []
    monkeypatch.setattr(
        bootstrap.os,
        "chmod",
        lambda path, _mode: chmod_paths.append(Path(os.fsdecode(path))),
    )
    bootstrap._atomic_write_private_file(target, "new\n")
    assert target.read_text() == "new\n"
    assert target not in chmod_paths


def test_windows_atomic_private_writer_shares_delete_and_replaces_while_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "state.json"
    target.write_text("old\n", encoding="utf-8")
    native_calls: list[dict[str, int]] = []
    native_descriptors: list[int] = []
    replaced_while_held = False
    original_replace = os.replace

    def create_file(path: Path, **kwargs: int) -> int:
        native_calls.append(kwargs)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        native_descriptors.append(descriptor)
        return descriptor

    def replace_while_open(source: Path, destination: Path) -> None:
        nonlocal replaced_while_held
        replaced_while_held = bool(native_descriptors) and all(
            os.fstat(descriptor).st_nlink == 1 for descriptor in native_descriptors
        )
        original_replace(source, destination)

    monkeypatch.setattr(bootstrap.os, "name", "nt")
    monkeypatch.setattr(bootstrap, "_windows_create_file", create_file)
    monkeypatch.setattr(bootstrap, "_windows_handle_to_fd", lambda handle: handle)
    monkeypatch.setattr(bootstrap.os, "replace", replace_while_open)

    bootstrap._atomic_write_private_file(target, "new\n")

    assert native_calls, "Windows private temp bypassed native CreateFileW"
    assert native_calls[0]["share"] & 0x00000004, "FILE_SHARE_DELETE was not requested"
    assert replaced_while_held, "os.replace ran after the verified temp handle was closed"
    assert target.read_text(encoding="utf-8") == "new\n"


def test_gitignore_covers_env_lock_and_backup_temps() -> None:
    root = Path(__file__).resolve().parents[2]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env.lock" in ignored
    assert ".env.tmp-*" in ignored
    assert ".backup-*.tmp" in ignored


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink contract")
def test_runtime_log_open_rejects_logs_symlink_and_final_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "logs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="runtime log"):
        bootstrap._open_runtime_log(tmp_path, tmp_path / "logs/api.log")

    (tmp_path / "logs").unlink()
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/api.log").symlink_to(outside / "captured.log")
    with pytest.raises(RuntimeError, match="runtime log"):
        bootstrap._open_runtime_log(tmp_path, tmp_path / "logs/api.log")


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link contract")
def test_runtime_log_open_rejects_hardlinked_final(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "api.log"
    log.touch()
    os.link(log, logs / "api.external")
    with pytest.raises(RuntimeError, match="private regular file"):
        bootstrap._open_runtime_log(tmp_path, log)


def test_windows_runtime_log_rejects_junction_component(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(bootstrap, "_lifecycle_uses_dir_fd", lambda: False)
    monkeypatch.setattr(bootstrap, "_path_is_link_or_junction", lambda path: path == logs)
    with pytest.raises(RuntimeError, match="runtime log"):
        bootstrap._open_runtime_log(tmp_path, logs / "worker.log")


def test_gitignore_covers_installer_coordination_and_retained_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".openbiliclaw-install-root.lock" in ignored
    assert ".obc-backup-source-*/" in ignored
    assert "installer-instance.json.tmp-*" in ignored


def test_initial_record_rejects_temporary_path_inode_swap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "installer.json"
    original_link = bootstrap.os.link

    def swap_before_link(source: object, destination: object, **kwargs: object) -> None:
        source_path = Path(os.fsdecode(source))
        source_path.rename(source_path.with_suffix(".held"))
        source_path.write_text("replacement-temp\n", encoding="utf-8")
        original_link(source_path, destination, **kwargs)

    monkeypatch.setattr(bootstrap.os, "link", swap_before_link)

    with pytest.raises(RuntimeError, match="ownership metadata"):
        bootstrap._atomic_create_private_file(target, "trusted-record\n")

    assert target.read_text(encoding="utf-8") == "replacement-temp\n"
    temporary_replacements = list(tmp_path.glob("installer.json.tmp-*"))
    assert len(temporary_replacements) == 1
    assert temporary_replacements[0].read_text(encoding="utf-8") == "replacement-temp\n"


def test_initial_record_never_path_chmods_a_published_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "installer.json"
    displaced = tmp_path / "published-held.json"
    original_chmod = bootstrap.os.chmod

    def swap_before_chmod(path: object, mode: int) -> None:
        path_value = Path(os.fsdecode(path))
        if path_value == target and not displaced.exists():
            target.rename(displaced)
            target.write_text("replacement-path\n", encoding="utf-8")
            original_chmod(target, 0o644)
        original_chmod(path_value, mode)

    monkeypatch.setattr(bootstrap.os, "chmod", swap_before_chmod)

    bootstrap._atomic_create_private_file(target, "trusted-record\n")

    assert target.read_text(encoding="utf-8") == "trusted-record\n"
    assert not displaced.exists()


def test_public_lifecycle_rejects_symlinked_data_descendant(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "data").symlink_to(outside, target_is_directory=True)

    with (
        pytest.raises(RuntimeError, match="symlink|contained"),
        bootstrap._lifecycle_lock(root, timeout=1.0),
    ):
        pytest.fail("symlinked data descendant entered lifecycle work")

    assert not (outside / "vnext").exists()


def test_public_direct_lifecycle_checks_each_descendant_for_windows_junction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    data = root / "data"
    observed: list[Path] = []
    original_check = bootstrap._path_is_link_or_junction

    def mark_data_as_junction(path: Path) -> bool:
        observed.append(path)
        return path == data or original_check(path)

    monkeypatch.setattr(bootstrap, "_lifecycle_uses_dir_fd", lambda: False)
    monkeypatch.setattr(bootstrap, "_path_is_link_or_junction", mark_data_as_junction)

    with (
        pytest.raises(RuntimeError, match="symlink|contained"),
        bootstrap._lifecycle_lock(root, timeout=1.0),
    ):
        pytest.fail("junction descendant entered lifecycle work")

    assert data in observed


def test_coherent_anchor_and_installer_pair_replacement_never_creates_second_holder(
    tmp_path: Path,
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    holder_entered = threading.Event()
    release_holder = threading.Event()
    second_started = threading.Event()
    second_entered = False

    def hold_original() -> None:
        with bootstrap._lifecycle_lock(tmp_path, timeout=2.0):
            holder_entered.set()
            assert release_holder.wait(2.0)

    def enter_replacement() -> None:
        nonlocal second_entered
        second_started.set()
        with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
            second_entered = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_original)
        assert holder_entered.wait(2.0)

        lock_path = tmp_path / bootstrap.LIFECYCLE_LOCK
        replacement = lock_path.with_suffix(".replacement")
        replacement.touch(mode=0o600)
        replacement_metadata = replacement.stat()
        replacement_id = str(bootstrap.uuid4())
        replacement.write_text(
            json.dumps(
                {
                    "version": 1,
                    "project_root": str(tmp_path.resolve()),
                    "device": replacement_metadata.st_dev,
                    "inode": replacement_metadata.st_ino,
                    "anchor_id": replacement_id,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(replacement, lock_path)

        state_path = tmp_path / bootstrap.INSTALLATION_STATE
        state_payload = json.loads(state_path.read_text())
        state_payload.update(
            lifecycle_anchor_id=replacement_id,
            lifecycle_anchor_device=replacement_metadata.st_dev,
            lifecycle_anchor_inode=replacement_metadata.st_ino,
        )
        bootstrap._atomic_write_private_file(
            state_path,
            json.dumps(state_payload, sort_keys=True) + "\n",
        )

        second = pool.submit(enter_replacement)
        assert second_started.wait(1.0)
        assert not second.done()
        release_holder.set()
        holder_error = holder.exception(timeout=2.0)
        second_error = second.exception(timeout=2.0)

    assert isinstance(second_error, RuntimeError)
    assert "lock identity changed" in str(second_error)
    assert isinstance(holder_error, RuntimeError)
    assert not second_entered


@pytest.mark.skipif(os.name == "nt", reason="POSIX root-directory flock contract")
def test_coherent_root_guard_anchor_and_installer_replacement_stays_serialized(
    tmp_path: Path,
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    holder_entered = threading.Event()
    release_holder = threading.Event()
    second_entered = False

    def hold_original() -> None:
        with (
            pytest.raises(RuntimeError, match="root lifecycle guard identity changed"),
            bootstrap._lifecycle_lock(tmp_path, timeout=2.0),
        ):
            holder_entered.set()
            assert release_holder.wait(2.0)

    def try_replacement() -> None:
        nonlocal second_entered
        with (
            pytest.raises(RuntimeError, match="timed out waiting"),
            bootstrap._lifecycle_lock(tmp_path, timeout=0.1),
        ):
            second_entered = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_original)
        assert holder_entered.wait(2.0)

        lock_path = tmp_path / bootstrap.LIFECYCLE_LOCK
        replacement = lock_path.with_suffix(".triple-replacement")
        replacement.touch(mode=0o600)
        replacement_metadata = replacement.stat()
        replacement_id = str(bootstrap.uuid4())
        replacement.write_text(
            json.dumps(
                {
                    "version": 1,
                    "project_root": str(tmp_path.resolve()),
                    "device": replacement_metadata.st_dev,
                    "inode": replacement_metadata.st_ino,
                    "anchor_id": replacement_id,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(replacement, lock_path)

        state_path = tmp_path / bootstrap.INSTALLATION_STATE
        state_payload = json.loads(state_path.read_text())
        state_payload.update(
            lifecycle_anchor_id=replacement_id,
            lifecycle_anchor_device=replacement_metadata.st_dev,
            lifecycle_anchor_inode=replacement_metadata.st_ino,
        )
        bootstrap._atomic_write_private_file(
            state_path,
            json.dumps(state_payload, sort_keys=True) + "\n",
        )
        guard_path = tmp_path / bootstrap.ROOT_LIFECYCLE_GUARD
        guard_replacement = guard_path.with_suffix(".triple-replacement")
        guard_replacement.write_text(
            json.dumps(state_payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        guard_replacement.chmod(0o600)
        os.replace(guard_replacement, guard_path)

        second = pool.submit(try_replacement)
        second.result(timeout=2.0)
        assert not second_entered
        assert not holder.done()
        release_holder.set()
        holder.result(timeout=2.0)


def test_bound_lifecycle_anchor_with_second_hardlink_fails_closed(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    lock_path = tmp_path / bootstrap.LIFECYCLE_LOCK
    os.link(lock_path, lock_path.with_suffix(".external-link"))

    with (
        pytest.raises(RuntimeError, match="lock identity changed"),
        bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
    ):
        pytest.fail("hardlinked bound anchor entered lifecycle work")


def test_waiter_revalidates_persistent_anchor_fields_before_yield(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        bootstrap._load_or_create_installation_state(tmp_path)

    holder_entered = threading.Event()
    release_holder = threading.Event()
    waiter_blocked = threading.Event()
    waiter_entered = False
    original_acquire = bootstrap._acquire_lock_before

    def observed_acquire(descriptor: int, *, path: Path, timeout: float | None) -> None:
        if threading.current_thread().name == "anchor-waiter":
            waiter_blocked.set()
        original_acquire(descriptor, path=path, timeout=timeout)

    def hold() -> None:
        with bootstrap._lifecycle_lock(tmp_path, timeout=2.0):
            holder_entered.set()
            assert release_holder.wait(2.0)

    def wait() -> None:
        nonlocal waiter_entered
        threading.current_thread().name = "anchor-waiter"
        with bootstrap._lifecycle_lock(tmp_path, timeout=2.0):
            waiter_entered = True

    monkeypatch.setattr(bootstrap, "_acquire_lock_before", observed_acquire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold)
        assert holder_entered.wait(2.0)
        waiter = pool.submit(wait)
        assert waiter_blocked.wait(2.0)
        path = tmp_path / bootstrap.INSTALLATION_STATE
        payload = json.loads(path.read_text())
        payload["lifecycle_anchor_id"] = "00000000-0000-4000-8000-000000000000"
        bootstrap._atomic_write_private_file(path, json.dumps(payload, sort_keys=True) + "\n")
        release_holder.set()
        with pytest.raises(RuntimeError, match="lock identity changed"):
            holder.result(timeout=2.0)
        with pytest.raises(RuntimeError, match="lock identity changed"):
            waiter.result(timeout=2.0)

    assert not waiter_entered


def test_generation_advance_refuses_to_preserve_tampered_anchor_fields(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError, match="lock identity changed"),
        bootstrap._lifecycle_lock(tmp_path, timeout=1.0),
    ):
        current = bootstrap._load_or_create_installation_state(tmp_path)
        path = tmp_path / bootstrap.INSTALLATION_STATE
        payload = json.loads(path.read_text())
        payload["lifecycle_anchor_id"] = "00000000-0000-4000-8000-000000000000"
        bootstrap._atomic_write_private_file(path, json.dumps(payload, sort_keys=True) + "\n")
        bootstrap._advance_installation_generation(tmp_path, current)

    assert json.loads(path.read_text())["generation"] == 0


def test_lifecycle_lock_rejects_symlinked_project_ancestor(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    with (
        pytest.raises(RuntimeError, match="symlinked project path"),
        bootstrap._lifecycle_lock(linked, timeout=1.0),
    ):
        pytest.fail("symlinked project path acquired lifecycle lock")


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


def test_failed_cleanup_retains_ownership_bound_runtime_state(tmp_path: Path) -> None:
    with bootstrap._lifecycle_lock(tmp_path, timeout=1.0):
        installation = bootstrap._load_or_create_installation_state(tmp_path)
        ownership = bootstrap._advance_installation_generation(tmp_path, installation)
        payload = _runtime_payload(ownership)
        bootstrap._atomic_write_private_file(
            tmp_path / bootstrap.PROCESS_STATE,
            json.dumps(payload, sort_keys=True) + "\n",
        )
        bootstrap._cleanup_failed_launch(
            tmp_path,
            ownership,
            [FakeProcess(1001), FakeProcess(1002)],
        )

    assert json.loads((tmp_path / bootstrap.PROCESS_STATE).read_text()) == payload


@pytest.mark.parametrize("kind", ("directory", "fifo"))
def test_stop_rejects_non_regular_runtime_state(tmp_path: Path, kind: str) -> None:
    state_path = tmp_path / bootstrap.PROCESS_STATE
    state_path.parent.mkdir(parents=True)
    if kind == "directory":
        state_path.mkdir()
    elif hasattr(os, "mkfifo"):
        os.mkfifo(state_path)
    else:
        pytest.skip("FIFO creation is unavailable")

    with pytest.raises(RuntimeError, match="regular file"):
        bootstrap._stop_managed_processes(tmp_path)


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
    assert (tmp_path / bootstrap.PROCESS_STATE).exists()
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
