"""Supervise the bundled tailnet helper without exposing its bootstrap secret."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, Protocol, cast

from openbiliclaw.proc import no_window_kwargs

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = 1
_HELPER_ENV = "OPENBILICLAW_TAILNET_HELPER"
_AUTH_KEY_ENV = "OPENBILICLAW_TAILNET_AUTH_KEY"
_STDERR_TAIL_LINES = 20
_STDERR_LINE_LIMIT = 2_000
_DRAIN_JOIN_TIMEOUT = 1.0
_SELF_TEST_TIMEOUT = 30.0

TailnetEvent = dict[str, object]
TailnetEventCallback = Callable[[TailnetEvent], None]


class TailnetSettings(Protocol):
    """Configuration fields required by the tailnet supervisor."""

    @property
    def enabled(self) -> bool: ...

    @property
    def hostname(self) -> str: ...


class TailnetRuntimeConfig(Protocol):
    """Small structural view of the application configuration."""

    @property
    def data_path(self) -> str | Path: ...

    @property
    def tailnet(self) -> TailnetSettings: ...


class TailnetSupervisorError(RuntimeError):
    """Base exception for tailnet helper discovery and lifecycle failures."""


class TailnetHelperNotFoundError(TailnetSupervisorError):
    """Raised when no usable bundled or installed helper can be found."""


class TailnetHelperExitedError(TailnetSupervisorError):
    """Raised after the helper exits unexpectedly with a non-zero code."""

    def __init__(self, returncode: int, detail: str = "") -> None:
        self.returncode = returncode
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Tailnet helper exited unexpectedly with code {returncode}{suffix}")


def tailnet_helper_basename() -> str:
    """Return the platform-specific helper executable name."""
    suffix = ".exe" if os.name == "nt" else ""
    return f"openbiliclaw-tailnet-helper{suffix}"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_tailnet_platform_supported() -> None:
    """Reject helper startup where the pinned Go toolchain cannot run it."""
    if sys.platform != "darwin":
        return
    release = platform.mac_ver()[0]
    try:
        major = int(release.split(".", 1)[0])
    except (TypeError, ValueError):
        return
    if major < 12:
        raise TailnetSupervisorError(
            "The embedded Tailnet helper requires macOS 12 or newer; "
            "local OpenBiliClaw remains available"
        )


def _tailnet_go_build_tags(source_dir: Path) -> str:
    """Read the canonical helper build tags shared by builds, tests, and notices."""
    path = source_dir / "build-tags.txt"
    try:
        tags = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TailnetSupervisorError(f"Tailnet helper build tags are missing: {path}") from exc
    if not tags or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_," for character in tags
    ):
        raise TailnetSupervisorError(f"Tailnet helper build tags are invalid: {path}")
    return tags


def _is_usable_helper(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def find_tailnet_helper(config: TailnetRuntimeConfig) -> Path:
    """Find the helper in the supported override, bundle, data, build, PATH order."""
    _ensure_tailnet_platform_supported()
    basename = tailnet_helper_basename()
    searched: list[Path] = []

    override = os.environ.get(_HELPER_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if _is_usable_helper(candidate):
            return candidate.resolve()
        reason = "does not exist" if not candidate.is_file() else "is not executable"
        raise TailnetHelperNotFoundError(f"{_HELPER_ENV} points to {candidate}, which {reason}")

    bundle_root = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_root, (str, os.PathLike)):
        searched.append(Path(bundle_root) / basename)

    data_helper = Path(config.data_path).expanduser() / "bin" / basename
    searched.extend((data_helper, _repository_root() / "build" / "tailnet" / basename))
    for candidate in searched:
        if candidate == data_helper and (candidate.parent.is_symlink() or candidate.is_symlink()):
            logger.warning("Ignoring Tailnet helper reached through a data/bin symlink")
            continue
        if _is_usable_helper(candidate):
            return candidate.resolve()

    installed = shutil.which(basename)
    if installed:
        candidate = Path(installed)
        if _is_usable_helper(candidate):
            return candidate.resolve()

    locations = ", ".join(str(path) for path in searched)
    raise TailnetHelperNotFoundError(
        f"Could not find {basename}; checked {locations or 'no filesystem locations'} and PATH"
    )


def _is_auth_key_environment_name(name: str) -> bool:
    normalized = name.upper().replace("-", "_")
    compact = normalized.replace("_", "")
    return "AUTH_KEY" in normalized or "AUTHKEY" in compact


def _private_child_environment() -> tuple[dict[str, str], str, tuple[str, ...]]:
    environment = dict(os.environ)
    auth_key = environment.get(_AUTH_KEY_ENV, "")
    secrets = tuple(
        sorted(
            {
                value
                for name, value in environment.items()
                if value and _is_auth_key_environment_name(name)
            },
            key=len,
            reverse=True,
        )
    )
    for name in tuple(environment):
        if _is_auth_key_environment_name(name):
            environment.pop(name, None)
    return environment, auth_key, secrets


def _is_sensitive_event_key(name: str) -> bool:
    compact = "".join(character for character in name.lower() if character.isalnum())
    return "authkey" in compact or "password" in compact or "secret" in compact


def _sanitize_event_value(value: object, secrets: tuple[str, ...] = ()) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            sanitized[key] = (
                "[redacted]"
                if _is_sensitive_event_key(key)
                else _sanitize_event_value(nested, secrets)
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_event_value(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
    return value


def _command_failure_detail(
    result: subprocess.CompletedProcess[str], secrets: tuple[str, ...]
) -> str:
    detail = (result.stderr or result.stdout).strip()
    for secret in secrets:
        detail = detail.replace(secret, "[redacted]")
    return detail[-_STDERR_LINE_LIMIT:]


def _passed_self_test(stdout: str) -> bool:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("protocol") == _PROTOCOL_VERSION
            and event.get("event") == "stopped"
        ):
            return True
    return False


def build_tailnet_helper(config: TailnetRuntimeConfig) -> Path:
    """Build, self-test, and atomically install the helper for a source checkout."""
    _ensure_tailnet_platform_supported()
    source_dir = _repository_root() / "cmd" / "openbiliclaw-tailnet"
    if not (source_dir / "go.mod").is_file():
        raise TailnetSupervisorError(f"Tailnet helper source is missing: {source_dir}")

    go_executable = shutil.which("go")
    if not go_executable:
        raise TailnetSupervisorError("Go is required to build the Tailnet helper")

    destination_dir = Path(config.data_path).expanduser() / "bin"
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination_dir.is_symlink() or not destination_dir.is_dir():
        raise TailnetSupervisorError(
            "Tailnet helper directory must be a real directory, not a file or symlink"
        )
    if os.name != "nt":
        os.chmod(destination_dir, 0o700)
    destination = destination_dir / tailnet_helper_basename()

    suffix = ".exe" if os.name == "nt" else ".tmp"
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=".openbiliclaw-tailnet-helper-",
        suffix=suffix,
        dir=destination_dir,
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    child_environment, _auth_key, secrets = _private_child_environment()
    child_environment["CGO_ENABLED"] = "0"
    build_command = [
        go_executable,
        "build",
        "-trimpath",
        f"-tags={_tailnet_go_build_tags(source_dir)}",
        "-ldflags=-s -w",
        "-o",
        str(candidate),
        ".",
    ]

    try:
        build_result = subprocess.run(
            build_command,
            cwd=source_dir,
            env=child_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **no_window_kwargs(),
        )
        if build_result.returncode != 0:
            detail = _command_failure_detail(build_result, secrets)
            suffix_text = f": {detail}" if detail else ""
            raise TailnetSupervisorError(
                f"Tailnet helper build failed with code {build_result.returncode}{suffix_text}"
            )
        if not candidate.is_file():
            raise TailnetSupervisorError(
                "Go reported success but did not create the Tailnet helper"
            )
        if os.name != "nt":
            os.chmod(candidate, 0o700)

        try:
            self_test = subprocess.run(
                [str(candidate), "--self-test"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_SELF_TEST_TIMEOUT,
                check=False,
                env=child_environment,
                **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise TailnetSupervisorError(
                f"Tailnet helper self-test timed out after {_SELF_TEST_TIMEOUT:g} seconds"
            ) from exc
        if self_test.returncode != 0 or not _passed_self_test(self_test.stdout):
            detail = _command_failure_detail(self_test, secrets)
            suffix_text = f": {detail}" if detail else ""
            raise TailnetSupervisorError(
                f"Tailnet helper self-test failed with code {self_test.returncode}{suffix_text}"
            )

        os.replace(candidate, destination)
        if os.name != "nt":
            os.chmod(destination, 0o700)
        return destination
    finally:
        with suppress(FileNotFoundError):
            candidate.unlink()


class TailnetSupervisor:
    """Own one helper process and its JSONL/status lifecycle."""

    def __init__(
        self,
        config: TailnetRuntimeConfig,
        api_port: int,
        *,
        event_callback: TailnetEventCallback | None = None,
    ) -> None:
        if not 1 <= api_port <= 65_535:
            raise ValueError(f"api_port must be between 1 and 65535, got {api_port}")
        hostname = config.tailnet.hostname.strip()
        if not hostname:
            raise TailnetSupervisorError("Tailnet hostname must not be empty")

        self._config = config
        self._api_port = api_port
        self._hostname = hostname
        self._event_callback = event_callback
        self._state_dir = Path(config.data_path).expanduser() / "tailnet"
        self._status_path = self._state_dir / "status.json"

        self._process: subprocess.Popen[str] | None = None
        self._helper_path: Path | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._last_event: TailnetEvent | None = None
        self._failure: TailnetHelperExitedError | None = None
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._secrets: tuple[str, ...] = ()
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._finished = threading.Event()

    @property
    def process(self) -> subprocess.Popen[str] | None:
        """Return the owned helper process, if start reached process creation."""
        with self._lock:
            return self._process

    @property
    def helper_path(self) -> Path | None:
        """Return the helper selected during start."""
        with self._lock:
            return self._helper_path

    @property
    def status_path(self) -> Path:
        """Return the durable status file path."""
        return self._status_path

    @property
    def last_event(self) -> TailnetEvent | None:
        """Return a copy of the most recently persisted event."""
        with self._lock:
            return dict(self._last_event) if self._last_event is not None else None

    @property
    def failure(self) -> TailnetHelperExitedError | None:
        """Return an asynchronous helper failure without raising it."""
        with self._lock:
            return self._failure

    def start(self) -> TailnetSupervisor:
        """Discover and start the helper, then send its one-line bootstrap message."""
        with self._lock:
            if self._process is not None:
                raise TailnetSupervisorError("Tailnet helper has already been started")

        helper_path = find_tailnet_helper(self._config)
        self._ensure_private_state_dir()
        child_environment, auth_key, secrets = _private_child_environment()
        self._secrets = secrets
        arguments = [
            str(helper_path),
            "--state-dir",
            str(self._state_dir),
            "--hostname",
            self._hostname,
            "--listen-port",
            str(self._api_port),
            "--backend-port",
            str(self._api_port),
        ]
        self._record_event({"protocol": _PROTOCOL_VERSION, "event": "starting"})

        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=child_environment,
                **no_window_kwargs(),
            )
        except OSError as exc:
            event = {
                "protocol": _PROTOCOL_VERSION,
                "event": "error",
                "code": "spawn_failed",
                "message": f"Unable to start tailnet helper: {exc}",
            }
            self._record_event(event)
            raise TailnetSupervisorError(str(event["message"])) from exc

        if process.stdin is None or process.stdout is None or process.stderr is None:
            with suppress(OSError):
                process.kill()
            raise TailnetSupervisorError("Tailnet helper was started without the required pipes")

        with self._lock:
            self._process = process
            self._helper_path = helper_path

        self._start_background_threads(process)
        bootstrap = {"protocol": _PROTOCOL_VERSION, "auth_key": auth_key}
        try:
            process.stdin.write(json.dumps(bootstrap, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._finished.wait(_DRAIN_JOIN_TIMEOUT)
            failure = self.failure
            if failure is not None:
                raise failure from exc
            self.stop(grace_timeout=0.0, terminate_timeout=_DRAIN_JOIN_TIMEOUT)
            raise TailnetSupervisorError(
                "Tailnet helper closed its bootstrap pipe before accepting configuration"
            ) from exc

        if process.poll() is not None:
            self._finished.wait(_DRAIN_JOIN_TIMEOUT)
            self.raise_if_failed()
        return self

    def wait(self, timeout: float | None = None) -> int:
        """Wait for helper exit, raising a clear exception for an unexpected crash."""
        process = self.process
        if process is None:
            raise TailnetSupervisorError("Tailnet helper has not been started")
        if not self._finished.wait(timeout):
            raise TimeoutError("Timed out waiting for the tailnet helper to exit")
        self.raise_if_failed()
        returncode = process.returncode
        return 0 if returncode is None else returncode

    def raise_if_failed(self) -> None:
        """Raise a failure recorded asynchronously by the process monitor."""
        failure = self.failure
        if failure is not None:
            raise failure

    def stop(self, *, grace_timeout: float = 2.0, terminate_timeout: float = 1.0) -> None:
        """Close stdin first, then escalate to terminate and kill when necessary."""
        process = self.process
        if process is None:
            return

        if process.poll() is None:
            self._stopping.set()
        stdin = process.stdin
        if stdin is not None and not stdin.closed:
            with suppress(BrokenPipeError, OSError):
                stdin.close()

        if not self._finished.wait(max(0.0, grace_timeout)):
            with suppress(OSError):
                process.terminate()
            if not self._finished.wait(max(0.0, terminate_timeout)):
                with suppress(OSError):
                    process.kill()
                self._finished.wait(max(0.0, terminate_timeout))
        self._join_background_threads()

    def __enter__(self) -> TailnetSupervisor:
        return self.start()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.stop()

    def _ensure_private_state_dir(self) -> None:
        try:
            self._state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise TailnetSupervisorError(
                f"Unable to create Tailnet state directory {self._state_dir}: {exc}"
            ) from exc
        if self._state_dir.is_symlink() or not self._state_dir.is_dir():
            raise TailnetSupervisorError(
                "Tailnet state directory must be a real directory, not a file or symlink"
            )
        if os.name != "nt":
            try:
                os.chmod(self._state_dir, 0o700)
            except OSError as exc:
                raise TailnetSupervisorError(
                    f"Unable to secure Tailnet state directory {self._state_dir}: {exc}"
                ) from exc

    def _start_background_threads(self, process: subprocess.Popen[str]) -> None:
        stdout = cast("IO[str]", process.stdout)
        stderr = cast("IO[str]", process.stderr)
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout,
            args=(stdout,),
            name="openbiliclaw-tailnet-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(stderr,),
            name="openbiliclaw-tailnet-stderr",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_process,
            args=(process,),
            name="openbiliclaw-tailnet-monitor",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._monitor_thread.start()

    def _drain_stdout(self, stream: IO[str]) -> None:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Tailnet helper wrote a non-JSON line to stdout")
                self._record_event(
                    {
                        "protocol": _PROTOCOL_VERSION,
                        "event": "error",
                        "code": "invalid_event",
                        "message": "Tailnet helper emitted invalid JSON",
                    }
                )
                continue
            if not isinstance(decoded, dict):
                logger.warning("Tailnet helper wrote a non-object JSON event")
                continue
            self._record_event(cast("TailnetEvent", decoded))

    def _drain_stderr(self, stream: IO[str]) -> None:
        for raw_line in stream:
            line = self._redact_text(raw_line.rstrip())[:_STDERR_LINE_LIMIT]
            if line:
                with self._lock:
                    self._stderr_tail.append(line)

    def _redact_text(self, value: str) -> str:
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def _monitor_process(self, process: subprocess.Popen[str]) -> None:
        returncode = process.wait()
        self._join_drain_threads()
        stopping = self._stopping.is_set()

        if returncode != 0 and not stopping:
            with self._lock:
                detail = " | ".join(self._stderr_tail)
                failure = TailnetHelperExitedError(returncode, detail)
                self._failure = failure
            self._record_event(
                {
                    "protocol": _PROTOCOL_VERSION,
                    "event": "error",
                    "code": "helper_exited",
                    "message": str(failure),
                    "returncode": returncode,
                }
            )
        else:
            last_event = self.last_event
            if last_event is None or last_event.get("event") != "stopped":
                self._record_event(
                    {
                        "protocol": _PROTOCOL_VERSION,
                        "event": "stopped",
                        "returncode": returncode,
                    }
                )
        self._finished.set()

    def _record_event(self, event: Mapping[str, object]) -> None:
        sanitized_value = _sanitize_event_value(dict(event), self._secrets)
        sanitized = cast("TailnetEvent", sanitized_value)
        with self._lock:
            self._last_event = sanitized
            try:
                self._write_status(sanitized)
            except OSError:
                logger.warning("Unable to persist Tailnet helper status", exc_info=True)
        callback = self._event_callback
        if callback is not None:
            try:
                callback(dict(sanitized))
            except Exception:
                logger.exception("Tailnet event callback failed")

    def _write_status(self, event: TailnetEvent) -> None:
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".status-", suffix=".tmp", dir=self._state_dir
        )
        temporary_path = Path(temporary_name)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as status_file:
                descriptor = -1
                status_file.write(payload)
                status_file.flush()
                os.fsync(status_file.fileno())
            os.replace(temporary_path, self._status_path)
            if os.name != "nt":
                os.chmod(self._status_path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                temporary_path.unlink()

    def _join_drain_threads(self) -> None:
        current = threading.current_thread()
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not current:
                thread.join(_DRAIN_JOIN_TIMEOUT)

    def _join_background_threads(self) -> None:
        current = threading.current_thread()
        for thread in (self._stdout_thread, self._stderr_thread, self._monitor_thread):
            if thread is not None and thread is not current:
                thread.join(_DRAIN_JOIN_TIMEOUT)


def start_tailnet_if_enabled(
    config: TailnetRuntimeConfig,
    api_port: int,
    *,
    event_callback: TailnetEventCallback | None = None,
) -> TailnetSupervisor | None:
    """Start a supervisor only when tailnet hosting is enabled in configuration."""
    if not config.tailnet.enabled:
        return None
    return TailnetSupervisor(config, api_port, event_callback=event_callback).start()


@contextmanager
def tailnet_runtime(
    config: TailnetRuntimeConfig,
    api_port: int,
    *,
    event_callback: TailnetEventCallback | None = None,
) -> Iterator[TailnetSupervisor | None]:
    """Run the optional helper for the duration of a context."""
    supervisor = start_tailnet_if_enabled(config, api_port, event_callback=event_callback)
    try:
        yield supervisor
    finally:
        if supervisor is not None:
            supervisor.stop()
