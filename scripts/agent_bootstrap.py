#!/usr/bin/env python3
"""Install and operate the vNext API and worker without legacy feature setup."""

from __future__ import annotations

import argparse
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

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420
DEFAULT_HEALTH_PATH = "/api/v1/system/readiness"
PROTECTED_CHECK_PATH = "/api/v1/settings"
RUNTIME_ENV_NAME = ".env"
PROCESS_STATE = Path("data/vnext/runtime-processes.json")


class ProcessLike(Protocol):
    pid: int


@dataclass(frozen=True, slots=True)
class InstallResult:
    status: str
    mode: str
    health_url: str
    api_pid: int | None = None
    worker_pid: int | None = None


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


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    if path.is_symlink():
        raise RuntimeError(f"refusing to use symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError(f"unable to open lock safely: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"lock is not a regular file: {path}")
        os.chmod(path, 0o600)
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            with suppress(OSError):
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
    if shutil.which("uv"):
        return ["uv", "run", "openbiliclaw"]
    executable = (
        project_dir
        / ".venv"
        / ("Scripts" if os.name == "nt" else "bin")
        / ("openbiliclaw.exe" if os.name == "nt" else "openbiliclaw")
    )
    if executable.exists():
        return [str(executable)]
    return [sys.executable, "-m", "openbiliclaw.cli"]


def _run_checked(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)  # noqa: S603


def _start_detached(
    command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path
) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab")
    try:
        options: dict[str, object] = {}
        if os.name == "nt":
            options["creationflags"] = 0x00000008 | 0x00000200
        else:
            options["start_new_session"] = True
        return subprocess.Popen(  # noqa: S603
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=stream,
            **options,
        )
    finally:
        stream.close()


def _write_process_state(project_dir: Path, *, api_pid: int, worker_pid: int) -> None:
    path = project_dir / PROCESS_STATE
    _atomic_write_private_file(
        path,
        json.dumps({"api": api_pid, "worker": worker_pid}, sort_keys=True) + "\n",
    )


def _stop_managed_processes(project_dir: Path) -> None:
    path = project_dir / PROCESS_STATE
    if path.is_symlink():
        raise RuntimeError(f"refusing to use symlink: {path}")
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return
    for value in state.values():
        if not isinstance(value, int) or value <= 1:
            continue
        with suppress(ProcessLookupError, PermissionError):
            os.kill(value, signal.SIGTERM)
    path.unlink(missing_ok=True)


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
                return response.status == 200
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
) -> InstallResult:
    """Prepare, migrate, and manage the source-install API and worker."""

    project_dir = project_dir.resolve()
    if install_dependencies:
        if shutil.which("uv"):
            run_command(["uv", "sync", "--frozen"], cwd=project_dir, env=dict(os.environ))
        else:
            run_command(
                [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
                cwd=project_dir,
                env=dict(os.environ),
            )
    values = ensure_local_runtime_environment(
        project_dir,
        litellm_base_url=litellm_base_url,
        litellm_api_key=litellm_api_key,
    )
    environment = _runtime_env(values)
    prefix = _command_prefix(project_dir)
    if start:
        _stop_managed_processes(project_dir)
    run_command([*prefix, "db", "migrate"], cwd=project_dir, env=environment)
    health_url = f"http://127.0.0.1:{port}{DEFAULT_HEALTH_PATH}"
    if not start:
        result = InstallResult(status="prepared", mode="local", health_url=health_url)
        _emit("complete", "local_runtime_prepared", mode="local", health_url=health_url)
        return result

    api = start_process(
        [*prefix, "serve", "--host", host, "--port", str(port)],
        cwd=project_dir,
        env=environment,
        log_path=project_dir / "logs/api.log",
    )
    try:
        worker = start_process(
            [*prefix, "worker"],
            cwd=project_dir,
            env=environment,
            log_path=project_dir / "logs/worker.log",
        )
    except BaseException:
        with suppress(ProcessLookupError, PermissionError):
            os.kill(api.pid, signal.SIGTERM)
        raise
    _write_process_state(project_dir, api_pid=api.pid, worker_pid=worker.pid)
    try:
        if not queue_probe(Path(values["OPENBILICLAW_HUEY_PATH"])):
            raise RuntimeError("worker queue did not initialize")
        run_command([*prefix, "doctor"], cwd=project_dir, env=environment)
        protected_ready = readiness_probe(host, port, values["OPENBILICLAW_ACCESS_TOKEN"])
    except BaseException:
        _stop_managed_processes(project_dir)
        raise
    if not protected_ready:
        _stop_managed_processes(project_dir)
        raise RuntimeError("protected readiness check failed")
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


def _install_docker_runtime(project_dir: Path, *, start: bool) -> InstallResult:
    values = ensure_docker_infrastructure_secrets(project_dir)
    health_url = f"http://127.0.0.1:{DEFAULT_PORT}{DEFAULT_HEALTH_PATH}"
    if not start:
        _emit("complete", "docker_runtime_prepared", mode="docker", health_url=health_url)
        return InstallResult(status="prepared", mode="docker", health_url=health_url)
    compose = _compose_prefix(project_dir)
    subprocess.run([*compose, "up", "-d", "--build"], cwd=project_dir, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [*compose, "exec", "-T", "api", "openbiliclaw", "db", "migrate"],
        cwd=project_dir,
        check=True,
    )
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
