"""Worker lifecycle contracts for mutable product runtime settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest

from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.infrastructure.jobs import worker
from openbiliclaw.infrastructure.jobs.queue import huey
from openbiliclaw.network import outbound_proxy_mode, outbound_proxy_url, set_outbound_proxy

if TYPE_CHECKING:
    from collections.abc import Callable


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

    def start(self) -> None:
        self._assert_active()
        if self._fail:
            raise RuntimeError("synthetic consumer failure")


def _configured_settings() -> UserSettings:
    return UserSettings.model_validate(
        {
            "network": {"mode": "custom", "proxy_url": "http://proxy.example:8080"},
            "logging": {"console_level": "DEBUG", "file_level": "ERROR"},
        }
    )


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
