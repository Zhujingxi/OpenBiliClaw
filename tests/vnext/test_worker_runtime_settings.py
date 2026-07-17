"""Worker lifecycle contracts for mutable product runtime settings."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context
from threading import Event, Thread
from time import monotonic
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.infrastructure.jobs import tasks as job_tasks
from openbiliclaw.infrastructure.jobs import worker
from openbiliclaw.infrastructure.jobs.queue import huey
from openbiliclaw.infrastructure.runtime_settings import applied_runtime_settings
from openbiliclaw.logging_setup import DeploymentLoggingSettings
from openbiliclaw.network import outbound_proxy_mode, outbound_proxy_url, set_outbound_proxy

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.connection import Connection


class _OwnedConsoleHandler(logging.StreamHandler[Any]):
    _openbiliclaw_sink = "console"


@dataclass
class _Service:
    assert_active: Callable[[], None]
    recovered: bool = False

    def recover_interrupted(self) -> None:
        self.assert_active()
        self.recovered = True


class _Consumer:
    def __init__(self, assert_active: Callable[[], None], *, fail: bool = False) -> None:
        self._assert_active = assert_active
        self._fail = fail

    def run(self) -> None:
        self._assert_active()
        if self._fail:
            raise RuntimeError("synthetic consumer failure")

    def stop(self, *, graceful: bool = False) -> None:
        assert graceful is True


def _configured_settings() -> UserSettings:
    return UserSettings.model_validate(
        {
            "network": {"mode": "custom", "proxy_url": "http://proxy.example:8080"},
            "logging": {"console_level": "DEBUG", "file_level": "ERROR"},
        }
    )


_CA_ENV_VARS = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def _system_settings() -> UserSettings:
    return UserSettings.model_validate({"network": {"mode": "system", "proxy_url": ""}})


def _seed_ca_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    monkeypatch.setenv("SSL_CERT_FILE", "/missing/test-ca-file.pem")
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/missing/test-requests-ca.pem")
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    return {name: os.environ.get(name) for name in _CA_ENV_VARS}


def _exercise_bounded_resistant_close(result_connection: Connection) -> None:
    worker.ASYNC_SHUTDOWN_TIMEOUT_SECONDS = 0.05
    started = Event()

    async def cancellation_resistant_job() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            stop_resisting_at = asyncio.get_running_loop().time() + 0.3
        else:
            return
        while asyncio.get_running_loop().time() < stop_resisting_at:
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                continue
        raise asyncio.CancelledError

    executor = worker.AsyncJobExecutor()
    caller = Thread(target=executor.run, args=(cancellation_resistant_job(),))
    caller.start()
    if not started.wait(timeout=1):
        result_connection.send(("error", "job did not start"))
        return

    before = monotonic()
    try:
        executor.close()
    except BaseException as error:
        result_connection.send(("error", type(error).__name__))
        return
    caller.join(timeout=1)
    result_connection.send(("ok", monotonic() - before, caller.is_alive()))


def test_runtime_settings_restore_ca_environment_after_normal_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _seed_ca_environment(monkeypatch)

    with applied_runtime_settings(_system_settings()):
        assert all(name not in os.environ for name in _CA_ENV_VARS)

    assert {name: os.environ.get(name) for name in _CA_ENV_VARS} == before


def test_runtime_settings_restore_ca_environment_after_exceptional_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _seed_ca_environment(monkeypatch)

    with (
        pytest.raises(RuntimeError, match="synthetic context failure"),
        applied_runtime_settings(_system_settings()),
    ):
        assert all(name not in os.environ for name in _CA_ENV_VARS)
        raise RuntimeError("synthetic context failure")

    assert {name: os.environ.get(name) for name in _CA_ENV_VARS} == before


def test_worker_applies_mutable_settings_before_recovery_and_restores_after_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    original_root_level = root.level
    original_mode = outbound_proxy_mode()
    original_url = outbound_proxy_url()
    host = logging.StreamHandler()
    host.setLevel(logging.CRITICAL)
    owned = _OwnedConsoleHandler()
    owned.setLevel(logging.WARNING)
    root.addHandler(host)
    root.addHandler(owned)
    calls: list[str] = []

    def assert_active() -> None:
        assert outbound_proxy_mode() == "custom"
        assert outbound_proxy_url() == "http://proxy.example:8080"
        assert host.level == logging.CRITICAL
        assert owned.level == logging.DEBUG
        assert root.level == original_root_level
        calls.append("active")

    service = _Service(assert_active)
    monkeypatch.setattr(
        huey,
        "create_consumer",
        lambda **_kwargs: _Consumer(assert_active),
    )

    try:
        worker.run_worker(
            cast("worker.RuntimeFactory", lambda: (service, {})),
            workers=1,
            settings_loader=_configured_settings,
        )
        assert service.recovered is True
        assert calls == ["active", "active"]
        assert outbound_proxy_mode() == original_mode
        assert outbound_proxy_url() == original_url
        assert host.level == logging.CRITICAL
        assert owned.level == logging.WARNING
        assert root.level == original_root_level
    finally:
        root.removeHandler(host)
        root.removeHandler(owned)
        set_outbound_proxy(original_url or "", mode=original_mode)


def test_worker_installs_real_owned_console_and_file_sinks_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    root = logging.getLogger()
    preexisting_owned = [
        handler
        for handler in root.handlers
        if getattr(handler, "_openbiliclaw_sink", None) in {"console", "file"}
    ]
    for handler in preexisting_owned:
        root.removeHandler(handler)
    host = logging.StreamHandler()
    host.setLevel(logging.CRITICAL)
    root.addHandler(host)
    deployment_logging = DeploymentLoggingSettings(
        directory=str(tmp_path),
        filename="worker-runtime.log",
    )
    seen: list[tuple[str, int]] = []

    def assert_active() -> None:
        owned = [
            (str(getattr(handler, "_openbiliclaw_sink", "")), handler.level)
            for handler in root.handlers
            if getattr(handler, "_openbiliclaw_sink", None) in {"console", "file"}
        ]
        assert sorted(owned) == [("console", logging.DEBUG), ("file", logging.ERROR)]
        assert host in root.handlers
        assert host.level == logging.CRITICAL
        seen[:] = owned

    monkeypatch.setattr(
        huey,
        "create_consumer",
        lambda **_kwargs: _Consumer(assert_active),
    )

    try:
        worker.run_worker(
            cast("worker.RuntimeFactory", lambda: (_Service(assert_active), {})),
            workers=1,
            settings_loader=_configured_settings,
            deployment_logging=deployment_logging,
        )
        assert seen
        assert host in root.handlers
        assert all(
            getattr(handler, "_openbiliclaw_sink", None) not in {"console", "file"}
            for handler in root.handlers
        )
        assert (tmp_path / "worker-runtime.log").is_file()
    finally:
        root.removeHandler(host)
        for handler in preexisting_owned:
            root.addHandler(handler)


def test_worker_restores_runtime_settings_when_consumer_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_mode = outbound_proxy_mode()
    original_url = outbound_proxy_url()
    owned = _OwnedConsoleHandler()
    owned.setLevel(logging.INFO)
    logging.getLogger().addHandler(owned)

    def assert_active() -> None:
        assert outbound_proxy_mode() == "custom"
        assert owned.level == logging.DEBUG

    monkeypatch.setattr(
        huey,
        "create_consumer",
        lambda **_kwargs: _Consumer(assert_active, fail=True),
    )

    try:
        with pytest.raises(RuntimeError, match="synthetic consumer failure"):
            worker.run_worker(
                cast(
                    "worker.RuntimeFactory",
                    lambda: (_Service(assert_active), {}),
                ),
                workers=1,
                settings_loader=_configured_settings,
            )
        assert outbound_proxy_mode() == original_mode
        assert outbound_proxy_url() == original_url
        assert owned.level == logging.INFO
    finally:
        logging.getLogger().removeHandler(owned)
        set_outbound_proxy(original_url or "", mode=original_mode)


def test_worker_async_executor_reuses_one_loop_for_sequential_and_concurrent_jobs() -> None:
    loop_ids: list[int] = []
    closed_on_loop: int | None = None

    async def loop_bound_job() -> None:
        loop_ids.append(id(asyncio.get_running_loop()))
        await asyncio.sleep(0.01)

    async def close_transport() -> None:
        nonlocal closed_on_loop
        closed_on_loop = id(asyncio.get_running_loop())

    async def failing_job() -> None:
        raise ValueError("synthetic async job failure")

    executor = worker.AsyncJobExecutor()
    executor.run(loop_bound_job())
    executor.run(loop_bound_job())
    with ThreadPoolExecutor(max_workers=4) as threads:
        futures = [threads.submit(executor.run, loop_bound_job()) for _index in range(4)]
        for future in futures:
            future.result(timeout=5)
    with pytest.raises(ValueError, match="synthetic async job failure"):
        executor.run(failing_job())
    executor.close(close_transport)
    with pytest.raises(RuntimeError, match="event loop is closed"):
        executor.run(loop_bound_job())

    assert len(loop_ids) == 6
    assert len(set(loop_ids)) == 1
    assert closed_on_loop == loop_ids[0]


def test_worker_joins_huey_threads_before_async_transport_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []

    class LifecycleConsumer:
        def run(self) -> None:
            lifecycle.append("consumer-returned")

        def stop(self, *, graceful: bool = False) -> None:
            assert graceful is True
            lifecycle.append("workers-joined")

    async def close_transport() -> None:
        assert lifecycle == ["consumer-returned", "workers-joined"]
        lifecycle.append("transport-closed")

    monkeypatch.setattr(huey, "create_consumer", lambda **_kwargs: LifecycleConsumer())
    service = _Service(lambda: None)

    worker.run_worker(
        lambda: worker.WorkerRuntime(service, {}, close_transport),
        workers=1,
        settings_loader=_system_settings,
    )

    assert lifecycle == ["consumer-returned", "workers-joined", "transport-closed"]


def test_sigterm_style_return_lets_active_job_finish_before_runtime_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    release = Event()
    outcomes: list[str] = []
    run_id = uuid4()

    class ActiveJobService:
        def recover_interrupted(self) -> None:
            return

        def claim(self, claimed_run_id: object) -> bool:
            assert claimed_run_id == run_id
            return True

        def checkpoint(self, checkpoint_run_id: object, progress: float) -> None:
            assert checkpoint_run_id == run_id
            assert 0 <= progress <= 1

        def succeed(self, succeeded_run_id: object) -> None:
            assert succeeded_run_id == run_id
            outcomes.append("succeeded")

        def fail(self, failed_run_id: object, _error: BaseException) -> None:
            assert failed_run_id == run_id
            outcomes.append("failed")

    async def active_handler(_run_id: object, _context: object) -> None:
        started.set()
        try:
            await asyncio.to_thread(release.wait)
        finally:
            release.set()

    class SigtermStyleConsumer:
        def __init__(self) -> None:
            self.worker_thread: Thread | None = None

        def run(self) -> None:
            self.worker_thread = Thread(
                target=job_tasks._run_job,
                args=("feed_replenishment", str(run_id), None),
            )
            self.worker_thread.start()
            assert started.wait(timeout=1)
            # Mirrors Huey's SIGTERM path: main supervision returns while the
            # daemon worker is still executing the claimed job.

        def stop(self, *, graceful: bool = False) -> None:
            assert graceful is True
            assert self.worker_thread is not None
            release.set()
            self.worker_thread.join(timeout=1)
            assert self.worker_thread.is_alive() is False

    monkeypatch.setattr(huey, "create_consumer", lambda **_kwargs: SigtermStyleConsumer())
    service = ActiveJobService()

    worker.run_worker(
        cast(
            "worker.RuntimeFactory",
            lambda: (service, {"feed_replenishment": active_handler}),
        ),
        workers=1,
        settings_loader=_system_settings,
    )

    assert outcomes == ["succeeded"]


def test_async_job_timeout_waits_for_cancellation_cleanup_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "ASYNC_JOB_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(worker, "ASYNC_SHUTDOWN_TIMEOUT_SECONDS", 1)
    cancellation_started = Event()
    allow_cleanup = Event()
    cleanup_finished = Event()
    runner_finished = Event()
    errors: list[BaseException] = []

    async def slow_job() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancellation_started.set()
            await asyncio.to_thread(allow_cleanup.wait)
            cleanup_finished.set()
            raise

    executor = worker.AsyncJobExecutor()

    def invoke() -> None:
        try:
            executor.run(slow_job())
        except BaseException as error:
            errors.append(error)
        finally:
            runner_finished.set()

    caller = Thread(target=invoke)
    caller.start()
    assert cancellation_started.wait(timeout=1)
    assert runner_finished.is_set() is False
    allow_cleanup.set()
    caller.join(timeout=1)

    assert caller.is_alive() is False
    assert cleanup_finished.is_set()
    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)
    executor.close()


def test_async_executor_close_abandons_cancellation_resistant_tasks_within_bound() -> None:
    context = get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_exercise_bounded_resistant_close,
        args=(child_connection,),
    )
    process.start()
    child_connection.close()
    process.join(timeout=2)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        pytest.fail("async executor close exceeded the subprocess safety bound")

    assert process.exitcode == 0
    assert parent_connection.poll(timeout=0.1)
    status, *details = parent_connection.recv()
    assert status == "ok", details
    elapsed, caller_alive = details
    assert elapsed < 0.5
    assert caller_alive is False
