#!/usr/bin/env python3
"""Install and operate the vNext API and worker without legacy feature setup."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420
DEFAULT_HEALTH_PATH = "/api/v1/system/readiness"
PROTECTED_CHECK_PATH = "/api/v1/settings"
RUNTIME_ENV_NAME = ".env"
PROCESS_STATE = Path("data/vnext/runtime-processes.json")
INSTALLATION_STATE = Path("data/vnext/installer-instance.json")
LIFECYCLE_LOCK = Path("data/vnext/install-lifecycle.lock")
LIFECYCLE_LOCK_TIMEOUT = 120.0


class ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Stable OS identity used to distinguish a managed process from PID reuse."""

    pid: int
    start_token: str
    executable: str
    argv_fingerprint: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "pid": self.pid,
            "start_token": self.start_token,
            "executable": self.executable,
            "argv_fingerprint": self.argv_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProcessIdentity | None:
        if not isinstance(value, dict):
            return None
        pid = value.get("pid")
        start_token = value.get("start_token")
        executable = value.get("executable")
        argv_fingerprint = value.get("argv_fingerprint")
        if (
            not isinstance(pid, int)
            or pid <= 1
            or not isinstance(start_token, str)
            or not start_token
            or not isinstance(executable, str)
            or not executable
            or not isinstance(argv_fingerprint, str)
            or len(argv_fingerprint) < 8
        ):
            return None
        return cls(pid, start_token, executable, argv_fingerprint)


@dataclass(frozen=True, slots=True)
class InstallResult:
    status: str
    mode: str
    health_url: str
    api_pid: int | None = None
    worker_pid: int | None = None


@dataclass(frozen=True, slots=True)
class InstallationState:
    """Canonical source-install identity and its latest runtime generation."""

    project_root: str
    instance_id: str
    generation: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "version": 1,
            "project_root": self.project_root,
            "instance_id": self.instance_id,
            "generation": self.generation,
        }

    @classmethod
    def from_dict(cls, value: object) -> InstallationState | None:
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        project_root = value.get("project_root")
        instance_id = value.get("instance_id")
        generation = value.get("generation")
        if (
            not isinstance(project_root, str)
            or not project_root
            or not isinstance(instance_id, str)
            or not _is_canonical_uuid(instance_id)
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            return None
        return cls(project_root, instance_id, generation)


def _emit(status: str, message: str, **details: object) -> None:
    """Emit a machine-readable event containing no credential values."""

    payload = {"status": status, "message": message, "details": details}
    print(f"BOOTSTRAP_STATUS:{json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


def _validate_env_value(name: str, value: str) -> str:
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be a single line")
    return value


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if path.is_symlink():
        raise RuntimeError(f"refusing to use symlink: {path}")
    if not path.exists():
        return [], {}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"unable to open runtime environment safely: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"runtime environment is not a regular file: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            lines = stream.read().splitlines()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def _lock_descriptor(descriptor: int, *, blocking: bool) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        flag = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK  # type: ignore[attr-defined]
        msvcrt.locking(descriptor, flag, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    flag = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    fcntl.flock(descriptor, flag)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _acquire_lock_before(descriptor: int, *, path: Path, timeout: float | None) -> None:
    if timeout is None:
        _lock_descriptor(descriptor, blocking=True)
        return
    if timeout < 0:
        raise ValueError("lock timeout must not be negative")
    deadline = time.monotonic() + timeout
    while True:
        try:
            _lock_descriptor(descriptor, blocking=False)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"timed out waiting for lifecycle lock: {path}")
        time.sleep(min(0.05, remaining))


@contextmanager
def _exclusive_lock(path: Path, *, timeout: float | None = None) -> Iterator[None]:
    if path.is_symlink():
        raise RuntimeError(f"refusing to use symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError(f"unable to open lock safely: {path}") from exc
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"lock is not a regular file: {path}")
        os.chmod(path, 0o600)
        _acquire_lock_before(descriptor, path=path, timeout=timeout)
        acquired = True
        yield
    finally:
        if acquired:
            with suppress(OSError):
                _unlock_descriptor(descriptor)
        os.close(descriptor)


@contextmanager
def _lifecycle_lock(project_dir: Path, *, timeout: float) -> Iterator[None]:
    """Serialize a complete source-install lifecycle across processes."""

    with _exclusive_lock(project_dir.resolve() / LIFECYCLE_LOCK, timeout=timeout):
        yield


def _atomic_write_private_file(path: Path, content: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _read_private_json(path: Path, *, description: str) -> object:
    if path.is_symlink():
        raise RuntimeError(f"refusing to use symlink: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"unable to open {description} safely: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"{description} is not a regular file: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            try:
                return json.load(stream)
            except ValueError as exc:
                raise RuntimeError(f"invalid {description}: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_or_create_installation_state(project_dir: Path) -> InstallationState:
    """Load a root-bound installer identity while the lifecycle lock is held."""

    project_dir = project_dir.resolve()
    path = project_dir / INSTALLATION_STATE
    try:
        value = _read_private_json(path, description="installer ownership metadata")
    except FileNotFoundError:
        created = InstallationState(str(project_dir), str(uuid4()), 0)
        _atomic_write_private_file(path, json.dumps(created.to_dict(), sort_keys=True) + "\n")
        return created
    parsed = InstallationState.from_dict(value)
    if parsed is None:
        raise RuntimeError(f"invalid installer ownership metadata: {path}")
    if parsed.project_root != str(project_dir):
        raise RuntimeError("installer ownership metadata belongs to a different project root")
    return parsed


def _advance_installation_generation(
    project_dir: Path, current: InstallationState
) -> InstallationState:
    """Allocate the next monotonic runtime owner while lifecycle-locked."""

    observed = _load_or_create_installation_state(project_dir)
    if observed != current:
        raise RuntimeError("installer ownership metadata changed during installation")
    advanced = InstallationState(
        current.project_root,
        current.instance_id,
        current.generation + 1,
    )
    _atomic_write_private_file(
        project_dir.resolve() / INSTALLATION_STATE,
        json.dumps(advanced.to_dict(), sort_keys=True) + "\n",
    )
    return advanced


def _merge_environment(path: Path, required: Mapping[str, str]) -> dict[str, str]:
    """Fill missing values while preserving stable, non-empty existing values."""

    with _exclusive_lock(path.with_name(f"{path.name}.lock")):
        lines, existing = _read_env(path)
        merged = dict(existing)
        for key, value in required.items():
            if not merged.get(key, "").strip():
                merged[key] = _validate_env_value(key, value)

        emitted: set[str] = set()
        output: list[str] = []
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                output.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key in merged:
                output.append(f"{key}={merged[key]}")
                emitted.add(key)
            else:
                output.append(line)
        for key, value in merged.items():
            if key not in emitted:
                output.append(f"{key}={value}")
        _atomic_write_private_file(path, "\n".join(output).rstrip("\n") + "\n")
        return merged


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def ensure_local_runtime_environment(
    project_dir: Path,
    *,
    litellm_base_url: str,
    litellm_api_key: str,
) -> dict[str, str]:
    """Persist stable source-install runtime settings in a mode-0600 env file."""

    project_dir = project_dir.resolve()
    env_path = project_dir / RUNTIME_ENV_NAME
    _lines, existing = _read_env(env_path)
    base_url = existing.get("OPENBILICLAW_LITELLM_BASE_URL", "") or litellm_base_url
    api_key = existing.get("OPENBILICLAW_LITELLM_API_KEY", "") or litellm_api_key
    if not base_url.strip() or not api_key.strip():
        raise ValueError("LiteLLM base URL and API key are required for source installs")
    data_dir = project_dir / "data/vnext"
    data_dir.mkdir(parents=True, exist_ok=True)
    required = {
        "OPENBILICLAW_SECRET_KEY": secrets.token_hex(32),
        "OPENBILICLAW_ACCESS_TOKEN": secrets.token_urlsafe(48),
        "OPENBILICLAW_LITELLM_BASE_URL": base_url,
        "OPENBILICLAW_LITELLM_API_KEY": api_key,
        "OPENBILICLAW_DATABASE_URL": _sqlite_url(data_dir / "openbiliclaw.db"),
        "OPENBILICLAW_HUEY_PATH": str((data_dir / "huey.db").resolve()),
    }
    return _merge_environment(env_path, required)


def ensure_docker_infrastructure_secrets(project_dir: Path) -> dict[str, str]:
    """Persist stable Compose infrastructure secrets without provider credentials."""

    required = {
        "LITELLM_POSTGRES_PASSWORD": secrets.token_hex(32),
        "LITELLM_MASTER_KEY": f"sk-{secrets.token_hex(32)}",
        "OPENBILICLAW_SECRET_KEY": secrets.token_hex(32),
        "OPENBILICLAW_ACCESS_TOKEN": secrets.token_urlsafe(48),
    }
    return _merge_environment(project_dir.resolve() / RUNTIME_ENV_NAME, required)


def _runtime_env(values: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(values)
    for key in ("NO_PROXY", "no_proxy"):
        current = [part for part in environment.get(key, "").split(",") if part]
        for host in ("localhost", "127.0.0.1", "::1"):
            if host not in current:
                current.append(host)
        environment[key] = ",".join(current)
    return environment


def _command_prefix(project_dir: Path) -> list[str]:
    executable = (
        project_dir
        / ".venv"
        / ("Scripts" if os.name == "nt" else "bin")
        / ("openbiliclaw.exe" if os.name == "nt" else "openbiliclaw")
    )
    if executable.exists():
        return [str(executable)]
    if shutil.which("uv"):
        return ["uv", "run", "openbiliclaw"]
    return [sys.executable, "-m", "openbiliclaw.cli"]


def _run_checked(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)  # noqa: S603


def _start_detached(
    command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path
) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab")
    try:
        if os.name == "nt":
            return subprocess.Popen(  # noqa: S603
                command,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=stream,
                creationflags=0x00000008 | 0x00000200,
            )
        return subprocess.Popen(  # noqa: S603
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    finally:
        stream.close()


def _command_fingerprint(command_line: bytes | str) -> str:
    payload = command_line if isinstance(command_line, bytes) else command_line.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _inspect_linux_process(pid: int) -> ProcessIdentity | None:
    process_dir = Path("/proc") / str(pid)
    try:
        first_stat = (process_dir / "stat").read_text(encoding="utf-8")
        executable = os.readlink(process_dir / "exe")
        command_line = (process_dir / "cmdline").read_bytes()
        second_stat = (process_dir / "stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    if first_stat != second_stat or not command_line:
        return None
    close_paren = first_stat.rfind(")")
    fields = first_stat[close_paren + 2 :].split() if close_paren >= 0 else []
    if len(fields) <= 19:
        return None
    return ProcessIdentity(
        pid=pid,
        start_token=fields[19],
        executable=executable,
        argv_fingerprint=_command_fingerprint(command_line),
    )


def _run_ps_field(pid: int, field: str) -> str | None:
    completed = subprocess.run(  # noqa: S603
        ["ps", "-ww", "-p", str(pid), "-o", f"{field}="],
        check=False,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _macos_process_details(pid: int) -> tuple[str, str] | None:
    """Return a microsecond-resolution start token and executable path."""

    import ctypes

    class ProcBsdInfo(ctypes.Structure):
        _fields_ = [
            ("pbi_flags", ctypes.c_uint32),
            ("pbi_status", ctypes.c_uint32),
            ("pbi_xstatus", ctypes.c_uint32),
            ("pbi_pid", ctypes.c_uint32),
            ("pbi_ppid", ctypes.c_uint32),
            ("pbi_uid", ctypes.c_uint32),
            ("pbi_gid", ctypes.c_uint32),
            ("pbi_ruid", ctypes.c_uint32),
            ("pbi_rgid", ctypes.c_uint32),
            ("pbi_svuid", ctypes.c_uint32),
            ("pbi_svgid", ctypes.c_uint32),
            ("rfu_1", ctypes.c_uint32),
            ("pbi_comm", ctypes.c_char * 16),
            ("pbi_name", ctypes.c_char * 32),
            ("pbi_nfiles", ctypes.c_uint32),
            ("pbi_pgid", ctypes.c_uint32),
            ("pbi_pjobc", ctypes.c_uint32),
            ("e_tdev", ctypes.c_uint32),
            ("e_tpgid", ctypes.c_uint32),
            ("pbi_nice", ctypes.c_int32),
            ("pbi_start_tvsec", ctypes.c_uint64),
            ("pbi_start_tvusec", ctypes.c_uint64),
        ]

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        info = ProcBsdInfo()
        size = ctypes.sizeof(info)
        read = libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
        if read != size or info.pbi_pid != pid:
            return None
        path_buffer = ctypes.create_string_buffer(4096)
        path_size = libproc.proc_pidpath(pid, path_buffer, len(path_buffer))
        if path_size <= 0:
            return None
        executable = path_buffer.value.decode("utf-8", errors="surrogateescape")
    except (OSError, ValueError):
        return None
    if not executable:
        return None
    return f"{info.pbi_start_tvsec}:{info.pbi_start_tvusec}", executable


def _inspect_macos_process(pid: int) -> ProcessIdentity | None:
    first_details = _macos_process_details(pid)
    command_line = _run_ps_field(pid, "command")
    second_details = _macos_process_details(pid)
    if first_details is None or first_details != second_details or command_line is None:
        return None
    start_token, executable = first_details
    return ProcessIdentity(
        pid=pid,
        start_token=start_token,
        executable=executable,
        argv_fingerprint=_command_fingerprint(command_line),
    )


def _inspect_posix_process(pid: int) -> ProcessIdentity | None:
    first_start = _run_ps_field(pid, "lstart")
    executable = _run_ps_field(pid, "comm")
    command_line = _run_ps_field(pid, "command")
    second_start = _run_ps_field(pid, "lstart")
    if (
        first_start is None
        or first_start != second_start
        or executable is None
        or command_line is None
    ):
        return None
    return ProcessIdentity(
        pid=pid,
        start_token=first_start,
        executable=executable,
        argv_fingerprint=_command_fingerprint(command_line),
    )


def _inspect_windows_process(pid: int) -> ProcessIdentity | None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        return None
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
        "if ($null -eq $p) { exit 3 }; "
        "@{CreationDate=$p.CreationDate;ExecutablePath=$p.ExecutablePath;"
        "CommandLine=$p.CommandLine}|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(  # noqa: S603
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except ValueError:
        return None
    start = value.get("CreationDate") if isinstance(value, dict) else None
    executable = value.get("ExecutablePath") if isinstance(value, dict) else None
    command_line = value.get("CommandLine") if isinstance(value, dict) else None
    if not isinstance(start, str) or not start:
        return None
    if not isinstance(executable, str) or not executable:
        return None
    if not isinstance(command_line, str) or not command_line:
        return None
    return ProcessIdentity(pid, start, executable, _command_fingerprint(command_line))


def _inspect_process(pid: int) -> ProcessIdentity | None:
    if pid <= 1:
        return None
    if os.name == "nt":
        return _inspect_windows_process(pid)
    if sys.platform.startswith("linux") and Path("/proc").is_dir():
        return _inspect_linux_process(pid)
    if sys.platform == "darwin":
        return _inspect_macos_process(pid)
    return _inspect_posix_process(pid)


def _write_process_state(
    project_dir: Path,
    *,
    ownership: InstallationState,
    api: ProcessIdentity,
    worker: ProcessIdentity,
) -> None:
    path = project_dir / PROCESS_STATE
    current = _load_or_create_installation_state(project_dir)
    if current != ownership:
        raise RuntimeError("runtime state ownership is no longer current")
    payload = {
        "version": 2,
        "project_root": ownership.project_root,
        "instance_id": ownership.instance_id,
        "generation": ownership.generation,
        "api": api.to_dict(),
        "worker": worker.to_dict(),
    }
    _atomic_write_private_file(
        path,
        json.dumps(payload, sort_keys=True) + "\n",
    )


def _process_state_ownership(value: object) -> InstallationState | None:
    if not isinstance(value, dict) or value.get("version") != 2:
        return None
    candidate = {
        "version": 1,
        "project_root": value.get("project_root"),
        "instance_id": value.get("instance_id"),
        "generation": value.get("generation"),
    }
    return InstallationState.from_dict(candidate)


def _read_process_state(
    path: Path,
) -> tuple[InstallationState, ProcessIdentity, ProcessIdentity]:
    value = _read_private_json(path, description="runtime process state")
    ownership = _process_state_ownership(value)
    if ownership is None or not isinstance(value, dict):
        raise RuntimeError(f"invalid runtime process state ownership: {path}")
    api = ProcessIdentity.from_dict(value.get("api"))
    worker = ProcessIdentity.from_dict(value.get("worker"))
    if api is None or worker is None:
        raise RuntimeError(f"invalid runtime process identity: {path}")
    return ownership, api, worker


def _remove_process_state_if_owned(project_dir: Path, ownership: InstallationState) -> bool:
    """Remove state only when it still belongs to the calling generation."""

    path = project_dir.resolve() / PROCESS_STATE
    if not path.exists() or path.is_symlink():
        return False
    try:
        value = _read_private_json(path, description="runtime process state")
    except (FileNotFoundError, RuntimeError):
        return False
    if _process_state_ownership(value) != ownership:
        return False
    path.unlink(missing_ok=True)
    return True


def _signal_process(pid: int, signum: int) -> None:
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if signum == signal.SIGKILL:
            command.append("/F")
        completed = subprocess.run(  # noqa: S603
            command, check=False, capture_output=True, text=True
        )
        if completed.returncode != 0:
            raise ProcessLookupError(pid)
        return
    if os.name != "nt":
        with suppress(ProcessLookupError, PermissionError):
            if os.getpgid(pid) == pid:
                os.killpg(pid, signum)
                return
    os.kill(pid, signum)


def _wait_for_identity_exit(identity: ProcessIdentity, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _inspect_process(identity.pid) != identity:
            return True
        time.sleep(0.05)
    return _inspect_process(identity.pid) != identity


def _stop_verified_identity(
    expected: ProcessIdentity,
    *,
    identity_probe: Callable[[int], ProcessIdentity | None],
    signal_process: Callable[[int, int], None],
    wait_for_exit: Callable[[ProcessIdentity, float], bool],
) -> bool:
    if identity_probe(expected.pid) != expected:
        return True
    with suppress(ProcessLookupError, PermissionError):
        signal_process(expected.pid, signal.SIGTERM)
    if wait_for_exit(expected, 5.0):
        return True
    if identity_probe(expected.pid) != expected:
        return True
    with suppress(ProcessLookupError, PermissionError):
        signal_process(expected.pid, signal.SIGKILL)
    return wait_for_exit(expected, 5.0)


def _stop_managed_processes(
    project_dir: Path,
    *,
    installation: InstallationState | None = None,
    identity_probe: Callable[[int], ProcessIdentity | None] = _inspect_process,
    signal_process: Callable[[int, int], None] = _signal_process,
    wait_for_exit: Callable[[ProcessIdentity, float], bool] = _wait_for_identity_exit,
) -> None:
    project_dir = project_dir.resolve()
    path = project_dir / PROCESS_STATE
    if path.is_symlink():
        raise RuntimeError(f"refusing to use symlink: {path}")
    if not path.is_file():
        return
    current = installation or _load_or_create_installation_state(project_dir)
    ownership, api, worker = _read_process_state(path)
    if ownership != current:
        raise RuntimeError("runtime process state ownership does not match this installation")
    survivors: list[str] = []
    for name, expected in (("api", api), ("worker", worker)):
        if not _stop_verified_identity(
            expected,
            identity_probe=identity_probe,
            signal_process=signal_process,
            wait_for_exit=wait_for_exit,
        ):
            survivors.append(name)
    if survivors:
        joined = ", ".join(survivors)
        raise RuntimeError(f"managed processes did not stop: {joined}")
    if not _remove_process_state_if_owned(project_dir, ownership):
        raise RuntimeError("runtime process state ownership changed while stopping")


def _signal_started_process(process: ProcessLike, signum: int) -> None:
    if isinstance(process, subprocess.Popen):
        _signal_process(process.pid, signum)
    elif signum == signal.SIGTERM:
        process.terminate()
    else:
        process.kill()


def _terminate_started_processes(processes: list[ProcessLike]) -> None:
    live = [process for process in processes if process.poll() is None]
    for process in live:
        with suppress(ProcessLookupError, PermissionError):
            _signal_started_process(process, signal.SIGTERM)
    for process in live:
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            if process.poll() is not None:
                continue
            with suppress(ProcessLookupError, PermissionError):
                _signal_started_process(process, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5.0)
    survivors = [process.pid for process in live if process.poll() is None]
    if survivors:
        joined = ", ".join(str(pid) for pid in survivors)
        raise RuntimeError(f"newly started processes did not stop: {joined}")


def _require_process_alive(process: ProcessLike, name: str) -> None:
    exit_code = process.poll()
    if exit_code is not None:
        raise RuntimeError(f"{name} exited before readiness (code {exit_code})")


def _probe_runtime(host: str, port: int, token: str, *, timeout: float = 30.0) -> bool:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    deadline = time.monotonic() + timeout
    public_url = f"http://{connect_host}:{port}{DEFAULT_HEALTH_PATH}"
    protected_url = f"http://{connect_host}:{port}{PROTECTED_CHECK_PATH}"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(public_url, timeout=2) as response:  # noqa: S310
                if response.status != 200:
                    raise RuntimeError("readiness unavailable")
            request = urllib.request.Request(  # noqa: S310
                protected_url, headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
                return int(response.status) == 200
        except (OSError, RuntimeError, urllib.error.URLError):
            time.sleep(0.25)
    return False


def _wait_for_file(path: Path, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and not path.is_symlink():
            return True
        time.sleep(0.1)
    return False


def _install_source_dependencies(project_dir: Path, run_command: Callable[..., None]) -> None:
    if shutil.which("uv"):
        run_command(["uv", "sync", "--frozen"], cwd=project_dir, env=dict(os.environ))
        return
    run_command(
        [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
        cwd=project_dir,
        env=dict(os.environ),
    )


def _cleanup_failed_launch(
    project_dir: Path,
    ownership: InstallationState,
    started: list[ProcessLike],
) -> None:
    try:
        _terminate_started_processes(started)
    finally:
        _remove_process_state_if_owned(project_dir, ownership)


def _launch_local_runtime(
    project_dir: Path,
    *,
    ownership: InstallationState,
    host: str,
    port: int,
    values: Mapping[str, str],
    environment: dict[str, str],
    prefix: list[str],
    run_command: Callable[..., None],
    start_process: Callable[..., ProcessLike],
    readiness_probe: Callable[..., bool],
    queue_probe: Callable[..., bool],
    identity_probe: Callable[[int], ProcessIdentity | None],
    state_writer: Callable[..., None],
) -> tuple[ProcessLike, ProcessLike]:
    started: list[ProcessLike] = []
    try:
        api = start_process(
            [*prefix, "serve", "--host", host, "--port", str(port)],
            cwd=project_dir,
            env=environment,
            log_path=project_dir / "logs/api.log",
        )
        started.append(api)
        _require_process_alive(api, "API")
        worker = start_process(
            [*prefix, "worker"],
            cwd=project_dir,
            env=environment,
            log_path=project_dir / "logs/worker.log",
        )
        started.append(worker)
        _require_process_alive(worker, "worker")
        api_identity = identity_probe(api.pid)
        worker_identity = identity_probe(worker.pid)
        if api_identity is None or worker_identity is None:
            raise RuntimeError("unable to verify launched process identity")
        state_writer(
            project_dir,
            ownership=ownership,
            api=api_identity,
            worker=worker_identity,
        )
        _verify_local_runtime(
            project_dir,
            host=host,
            port=port,
            values=values,
            environment=environment,
            prefix=prefix,
            api=api,
            worker=worker,
            run_command=run_command,
            readiness_probe=readiness_probe,
            queue_probe=queue_probe,
        )
        return api, worker
    except BaseException:
        _cleanup_failed_launch(project_dir, ownership, started)
        raise


def _verify_local_runtime(
    project_dir: Path,
    *,
    host: str,
    port: int,
    values: Mapping[str, str],
    environment: dict[str, str],
    prefix: list[str],
    api: ProcessLike,
    worker: ProcessLike,
    run_command: Callable[..., None],
    readiness_probe: Callable[..., bool],
    queue_probe: Callable[..., bool],
) -> None:
    _require_process_alive(api, "API")
    _require_process_alive(worker, "worker")
    if not queue_probe(Path(values["OPENBILICLAW_HUEY_PATH"])):
        raise RuntimeError("worker queue did not initialize")
    _require_process_alive(api, "API")
    _require_process_alive(worker, "worker")
    run_command([*prefix, "doctor"], cwd=project_dir, env=environment)
    _require_process_alive(api, "API")
    _require_process_alive(worker, "worker")
    protected_ready = readiness_probe(host, port, values["OPENBILICLAW_ACCESS_TOKEN"])
    _require_process_alive(api, "API")
    _require_process_alive(worker, "worker")
    if not protected_ready:
        raise RuntimeError("protected readiness check failed")


def install_local_runtime(
    project_dir: Path,
    *,
    host: str,
    port: int,
    litellm_base_url: str,
    litellm_api_key: str,
    install_dependencies: bool = True,
    start: bool = True,
    run_command: Callable[..., None] = _run_checked,
    start_process: Callable[..., ProcessLike] = _start_detached,
    readiness_probe: Callable[..., bool] = _probe_runtime,
    queue_probe: Callable[..., bool] = _wait_for_file,
    identity_probe: Callable[[int], ProcessIdentity | None] = _inspect_process,
    signal_process: Callable[[int, int], None] = _signal_process,
    wait_for_exit: Callable[[ProcessIdentity, float], bool] = _wait_for_identity_exit,
    state_writer: Callable[..., None] = _write_process_state,
    lifecycle_timeout: float = LIFECYCLE_LOCK_TIMEOUT,
) -> InstallResult:
    """Prepare, migrate, and manage the source-install API and worker."""

    project_dir = project_dir.resolve()
    health_url = f"http://127.0.0.1:{port}{DEFAULT_HEALTH_PATH}"
    with _lifecycle_lock(project_dir, timeout=lifecycle_timeout):
        installation = _load_or_create_installation_state(project_dir)
        if install_dependencies:
            _install_source_dependencies(project_dir, run_command)
        values = ensure_local_runtime_environment(
            project_dir,
            litellm_base_url=litellm_base_url,
            litellm_api_key=litellm_api_key,
        )
        environment = _runtime_env(values)
        prefix = _command_prefix(project_dir)
        _stop_managed_processes(
            project_dir,
            installation=installation,
            identity_probe=identity_probe,
            signal_process=signal_process,
            wait_for_exit=wait_for_exit,
        )
        if start:
            installation = _advance_installation_generation(project_dir, installation)
        run_command([*prefix, "db", "migrate"], cwd=project_dir, env=environment)
        if not start:
            result = InstallResult(status="prepared", mode="local", health_url=health_url)
            _emit("complete", "local_runtime_prepared", mode="local", health_url=health_url)
            return result
        api, worker = _launch_local_runtime(
            project_dir,
            ownership=installation,
            host=host,
            port=port,
            values=values,
            environment=environment,
            prefix=prefix,
            run_command=run_command,
            start_process=start_process,
            readiness_probe=readiness_probe,
            queue_probe=queue_probe,
            identity_probe=identity_probe,
            state_writer=state_writer,
        )
        result = InstallResult(
            status="complete",
            mode="local",
            health_url=health_url,
            api_pid=api.pid,
            worker_pid=worker.pid,
        )
        _emit("complete", "local_runtime_ready", mode="local", health_url=health_url)
        return result


def _compose_prefix(project_dir: Path) -> list[str]:
    if (project_dir / "docker-compose.yml").is_file():
        return ["docker", "compose"]
    if (project_dir / "docker-compose.prebuilt.yml").is_file():
        return ["docker", "compose", "-f", "docker-compose.prebuilt.yml"]
    raise RuntimeError("no supported Compose file found")


def _compose_status_rows(output: str) -> dict[str, dict[str, object]]:
    """Normalize Compose's array and newline-delimited JSON output forms."""

    try:
        parsed = json.loads(output)
        values = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        try:
            values = [json.loads(line) for line in output.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise RuntimeError("unable to read Docker Compose service status") from exc
    rows: dict[str, dict[str, object]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        service = value.get("Service")
        if isinstance(service, str):
            rows[service] = value
    return rows


def _wait_for_docker_runtime(
    project_dir: Path, compose: list[str], *, timeout: float = 90.0
) -> None:
    """Require successful migration plus healthy API and worker containers."""

    deadline = time.monotonic() + timeout
    command = [
        *compose,
        "ps",
        "--all",
        "--format",
        "json",
        "migrate",
        "api",
        "worker",
    ]
    while time.monotonic() < deadline:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=project_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        rows = _compose_status_rows(result.stdout)
        migration = rows.get("migrate")
        api = rows.get("api")
        worker = rows.get("worker")
        if migration is not None:
            migration_exit = migration.get("ExitCode")
            if migration_exit not in (None, 0, "0"):
                raise RuntimeError("migration service failed")
        if worker is not None and (
            str(worker.get("State", "")).lower() in {"dead", "exited", "restarting"}
            or str(worker.get("Health", "")).lower() == "unhealthy"
        ):
            raise RuntimeError("worker failed before becoming healthy")
        if api is not None and (
            str(api.get("State", "")).lower() in {"dead", "exited", "restarting"}
            or str(api.get("Health", "")).lower() == "unhealthy"
        ):
            raise RuntimeError("api failed before becoming healthy")
        if (
            migration is not None
            and str(migration.get("State", "")).lower() == "exited"
            and migration.get("ExitCode") in (0, "0")
            and api is not None
            and str(api.get("State", "")).lower() == "running"
            and str(api.get("Health", "")).lower() == "healthy"
            and worker is not None
            and str(worker.get("State", "")).lower() == "running"
            and str(worker.get("Health", "")).lower() == "healthy"
        ):
            return
        time.sleep(0.25)
    raise RuntimeError("Docker runtime did not become healthy")


def _install_docker_runtime(project_dir: Path, *, start: bool) -> InstallResult:
    values = ensure_docker_infrastructure_secrets(project_dir)
    health_url = f"http://127.0.0.1:{DEFAULT_PORT}{DEFAULT_HEALTH_PATH}"
    compose = _compose_prefix(project_dir)
    if not start:
        subprocess.run(  # noqa: S603
            [*compose, "run", "--rm", "migrate"], cwd=project_dir, check=True
        )
        _emit("complete", "docker_runtime_prepared", mode="docker", health_url=health_url)
        return InstallResult(status="prepared", mode="docker", health_url=health_url)
    subprocess.run([*compose, "up", "-d", "--build"], cwd=project_dir, check=True)  # noqa: S603
    _wait_for_docker_runtime(project_dir, compose)
    if not _probe_runtime(
        "127.0.0.1", DEFAULT_PORT, values["OPENBILICLAW_ACCESS_TOKEN"], timeout=90
    ):
        raise RuntimeError("protected readiness check failed")
    _emit("complete", "docker_runtime_ready", mode="docker", health_url=health_url)
    return InstallResult(status="complete", mode="docker", health_url=health_url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the OpenBiliClaw vNext runtime")
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--mode", choices=("auto", "docker", "local"), default="auto")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--litellm-base-url",
        default=os.getenv("OPENBILICLAW_LITELLM_BASE_URL", ""),
        help="User-managed LiteLLM OpenAI-compatible base URL (source installs)",
    )
    parser.add_argument("--skip-install", action="store_true", help="Skip dependency installation")
    parser.add_argument("--skip-start", action="store_true", help="Prepare and migrate only")
    return parser


def run(args: argparse.Namespace) -> InstallResult:
    project_dir = Path(args.project_dir).expanduser().resolve()
    mode = args.mode
    if mode == "auto":
        mode = "docker" if shutil.which("docker") else "local"
    if mode == "docker":
        return _install_docker_runtime(project_dir, start=not args.skip_start)
    return install_local_runtime(
        project_dir,
        host=args.host,
        port=args.port,
        litellm_base_url=args.litellm_base_url,
        litellm_api_key=os.getenv("OPENBILICLAW_LITELLM_API_KEY", ""),
        install_dependencies=not args.skip_install,
        start=not args.skip_start,
    )


def main() -> int:
    try:
        result = run(build_parser().parse_args())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        _emit("error", "bootstrap_failed", error_type=type(exc).__name__)
        print(f"OpenBiliClaw install failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"OpenBiliClaw {result.mode} runtime: {result.status}")
    print(f"Readiness: {result.health_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "InstallResult",
    "_compose_prefix",
    "build_parser",
    "ensure_docker_infrastructure_secrets",
    "ensure_local_runtime_environment",
    "install_local_runtime",
    "main",
    "run",
]
