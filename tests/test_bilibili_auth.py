"""Tests for Bilibili cookie authentication management."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from openbiliclaw.bilibili.api import BilibiliAPIError, BilibiliAuthExpiredError, NavInfo
from openbiliclaw.bilibili.auth import AuthManager, resolve_runtime_cookie


class FakeNavClient:
    """Minimal fake nav client for auth tests."""

    def __init__(
        self,
        *,
        nav_info: NavInfo | None = None,
        error: Exception | None = None,
    ) -> None:
        self.nav_info = nav_info
        self.error = error
        self.closed = False

    async def get_nav_info(self) -> NavInfo:
        if self.error is not None:
            raise self.error
        return self.nav_info or NavInfo(is_login=False)

    async def close(self) -> None:
        self.closed = True


def test_auth_manager_persists_and_loads_cookie(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)

    manager.set_cookie("  SESSDATA=abc123; bili_jct=xyz  ")

    reloaded = AuthManager(tmp_path)
    assert reloaded.load_cookie() == "SESSDATA=abc123; bili_jct=xyz"


@pytest.mark.asyncio
async def test_validate_cookie_returns_authenticated_status(tmp_path: Path) -> None:
    fake_client = FakeNavClient(
        nav_info=NavInfo(is_login=True, uname="alice", mid=10086),
    )
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=abc123")

    assert status.has_cookie is True
    assert status.authenticated is True
    assert status.username == "alice"
    assert status.user_id == 10086
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_validate_cookie_returns_failure_reason(tmp_path: Path) -> None:
    fake_client = FakeNavClient(error=BilibiliAPIError("cookie 已过期"))
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=expired")

    assert status.has_cookie is True
    assert status.authenticated is False
    assert "已过期" in status.message
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_validate_cookie_flags_transport_errors_as_network(tmp_path: Path) -> None:
    """Generic exceptions (proxy, DNS, reset) are transport-class failures."""
    fake_client = FakeNavClient(error=ConnectionError("All connection attempts failed"))
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=valid-but-proxied")

    assert status.authenticated is False
    assert status.network_error is True


@pytest.mark.asyncio
async def test_validate_cookie_expired_session_is_not_a_network_error(tmp_path: Path) -> None:
    """A -101 nav answer means the request went through — the cookie is the
    problem, so network_error must stay False or the UI points at the proxy."""
    fake_client = FakeNavClient(
        error=BilibiliAuthExpiredError("Bilibili session expired on /x/web-interface/nav (-101)")
    )
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=expired")

    assert status.authenticated is False
    assert status.network_error is False
    assert "未登录或已失效" in status.message
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_validate_cookie_not_logged_in_is_not_a_network_error(tmp_path: Path) -> None:
    fake_client = FakeNavClient(nav_info=NavInfo(is_login=False))
    manager = AuthManager(
        tmp_path,
        api_client_factory=lambda cookie: fake_client,
    )

    status = await manager.validate_cookie("SESSDATA=anonymous")

    assert status.authenticated is False
    assert status.network_error is False


@pytest.mark.asyncio
async def test_get_status_reports_missing_cookie(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)

    status = await manager.get_status()

    assert status.has_cookie is False
    assert status.authenticated is False
    assert "未配置" in status.message


def test_resolve_runtime_cookie_prefers_config_value(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)
    manager.set_cookie("SESSDATA=saved_cookie")

    assert (
        resolve_runtime_cookie(data_dir=tmp_path, configured_cookie="SESSDATA=config_cookie")
        == "SESSDATA=config_cookie"
    )


def test_resolve_runtime_cookie_falls_back_to_saved_cookie(tmp_path: Path) -> None:
    manager = AuthManager(tmp_path)
    manager.set_cookie("SESSDATA=saved_cookie")

    assert (
        resolve_runtime_cookie(data_dir=tmp_path, configured_cookie="") == "SESSDATA=saved_cookie"
    )


def test_default_factory_builds_client_with_configured_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[bilibili].proxy must reach the probe client built by AuthManager."""
    captured: dict[str, object] = {}

    class _CaptureClient:
        def __init__(self, cookie: str = "", *, proxy: str | None = None) -> None:
            captured["cookie"] = cookie
            captured["proxy"] = proxy

    monkeypatch.setattr("openbiliclaw.bilibili.api.BilibiliAPIClient", _CaptureClient)

    manager = AuthManager(tmp_path, proxy="http://10.0.0.1:8080")
    manager._default_api_client_factory("SESSDATA=abc")
    assert captured == {"cookie": "SESSDATA=abc", "proxy": "http://10.0.0.1:8080"}

    manager_direct = AuthManager(tmp_path)
    manager_direct._default_api_client_factory("SESSDATA=abc")
    assert captured["proxy"] is None
