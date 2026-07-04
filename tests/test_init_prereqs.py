"""Tests for InitPrereqs cached probes (gui-init plan C1)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openbiliclaw.runtime import init_prereqs
from openbiliclaw.runtime.init_prereqs import InitPrereqs


class _Provider:
    def __init__(self, ok: bool) -> None:
        self._ok = ok
        self.calls = 0

    async def health_check(self) -> bool:
        self.calls += 1
        return self._ok


def _ctx(
    *, provider: Any = None, cookie: str = "", platforms: dict[str, bool] | None = None
) -> Any:
    registry = SimpleNamespace(get=lambda: provider) if provider is not None else None
    platforms = platforms or {}
    sources = SimpleNamespace(
        **{
            name: SimpleNamespace(enabled=platforms.get(name, False))
            for name in init_prereqs._PLATFORM_SOURCE_FIELDS
        }
    )
    config = SimpleNamespace(
        bilibili=SimpleNamespace(cookie=cookie), sources=sources, data_path=None
    )
    return SimpleNamespace(llm_registry=registry, config=config)


async def test_chat_ready_true_and_cached() -> None:
    provider = _Provider(ok=True)
    pr = InitPrereqs(_ctx(provider=provider))
    assert await pr.chat_ready() is True
    assert await pr.chat_ready() is True  # cached
    assert provider.calls == 1  # single probe within TTL


async def test_chat_ready_false_when_provider_unhealthy() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=False)))
    assert await pr.chat_ready() is False


async def test_chat_ready_false_when_no_registry() -> None:
    pr = InitPrereqs(_ctx(provider=None))
    assert await pr.chat_ready() is False


async def test_bilibili_check_failed_without_cookie() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie=""))
    assert await pr.bilibili_check() == "failed"


async def test_bilibili_check_ok_and_cached(monkeypatch: Any) -> None:
    calls = {"n": 0}

    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            calls["n"] += 1
            return SimpleNamespace(authenticated=True)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="sessdata=abc"))
    assert await pr.bilibili_check() == "ok"
    assert await pr.bilibili_check() == "ok"  # cached (60s success TTL)
    assert calls["n"] == 1


async def test_bilibili_check_failed_when_unauthenticated(monkeypatch: Any) -> None:
    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(authenticated=False)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="bad"))
    assert await pr.bilibili_check() == "failed"


async def test_bilibili_detail_empty_on_success(monkeypatch: Any) -> None:
    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(authenticated=True)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="sessdata=abc"))
    assert await pr.bilibili_check() == "ok"
    assert pr.peek_bilibili_detail() == ""


async def test_bilibili_detail_without_cookie_names_the_cookie() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie=""))
    assert await pr.bilibili_check() == "failed"
    assert "Cookie" in pr.peek_bilibili_detail()
    assert "代理" not in pr.peek_bilibili_detail()


async def test_bilibili_detail_cookie_invalid_has_no_proxy_hint(monkeypatch: Any) -> None:
    """An expired cookie is a cookie problem — pointing at the proxy would
    send the user down exactly the wrong rabbit hole."""

    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(
                authenticated=False,
                message="当前 Cookie 未登录或已失效。",
                network_error=False,
            )

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="expired"))
    assert await pr.bilibili_check() == "failed"
    assert pr.peek_bilibili_detail() == "当前 Cookie 未登录或已失效。"


async def test_bilibili_detail_network_failure_carries_proxy_hint(monkeypatch: Any) -> None:
    """Transport-class failures (proxy/risk-control/DNS) must tell the user to
    check their proxy — a valid cookie + proxied probe shows as 'not logged
    in' otherwise (field report 2026-07)."""

    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(
                authenticated=False,
                message="Connection reset by peer",
                network_error=True,
            )

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="sessdata=abc"))
    assert await pr.bilibili_check() == "failed"
    detail = pr.peek_bilibili_detail()
    assert "Connection reset by peer" in detail
    assert "代理" in detail


async def test_bilibili_detail_timeout_carries_proxy_hint(monkeypatch: Any) -> None:
    import asyncio

    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            await asyncio.sleep(0.2)
            return SimpleNamespace(authenticated=True)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    monkeypatch.setattr(init_prereqs, "_BILI_PROBE_TIMEOUT", 0.01)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="sessdata=abc"))
    assert await pr.bilibili_check() == "failed"
    detail = pr.peek_bilibili_detail()
    assert "超时" in detail
    assert "代理" in detail


def test_enabled_platforms_reads_config() -> None:
    pr = InitPrereqs(_ctx(platforms={"bilibili": True, "douyin": True}))
    assert pr.enabled_platforms() == ["bilibili", "douyin"]


async def test_bilibili_check_passes_configured_proxy_to_auth_manager(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _FakeAuth:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(authenticated=True)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    ctx = _ctx(provider=_Provider(ok=True), cookie="sessdata=abc")
    ctx.config.bilibili.proxy = "http://10.0.0.1:8080"
    pr = InitPrereqs(ctx)
    assert await pr.bilibili_check() == "ok"
    assert captured["proxy"] == "http://10.0.0.1:8080"


async def test_bilibili_check_defaults_to_direct_connection(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _FakeAuth:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(authenticated=True)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="sessdata=abc"))
    assert await pr.bilibili_check() == "ok"
    assert captured["proxy"] is None


async def test_bilibili_detail_blames_explicit_proxy_when_configured(monkeypatch: Any) -> None:
    """With an explicit [bilibili].proxy the transport failed on THAT proxy —
    telling the user "we bypassed your proxy" would be a lie."""

    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(
                authenticated=False,
                message="All connection attempts failed",
                network_error=True,
            )

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    ctx = _ctx(provider=_Provider(ok=True), cookie="sessdata=abc")
    ctx.config.bilibili.proxy = "http://10.0.0.1:8080"
    pr = InitPrereqs(ctx)
    assert await pr.bilibili_check() == "failed"
    detail = pr.peek_bilibili_detail()
    assert "[bilibili] proxy" in detail
    assert "http://10.0.0.1:8080" in detail
    assert "绕过系统代理" not in detail
