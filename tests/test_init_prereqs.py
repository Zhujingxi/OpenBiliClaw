"""Tests for InitPrereqs cached probes (gui-init plan C1)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
from openbiliclaw.runtime import init_prereqs
from openbiliclaw.runtime.init_prereqs import InitPrereqs

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean_probe_store() -> Iterator[None]:
    """Start every case with no B站 verdict on record.

    ``InitPrereqs`` no longer keeps a private B站 cache: its verdict lives in
    the process-wide ``LIVE_PROBES``, shared with ``GET /api/sources/status`` so
    the two surfaces cannot hold opposite answers about one cookie (spec D3).
    Process-wide is the point, which means a verdict recorded by one case would
    otherwise satisfy the next case's TTL check and suppress the probe it is
    trying to observe. Constructing a fresh ``InitPrereqs`` is no longer enough
    isolation — the store has to be cleared too.
    """
    LIVE_PROBES.clear()
    yield
    LIVE_PROBES.clear()


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


async def test_cached_readiness_exists_after_both_preflight_checks() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie=""))
    assert pr.has_cached_readiness() is False
    assert await pr.chat_ready() is True
    assert pr.has_cached_readiness() is False
    assert await pr.bilibili_check() == "failed"
    assert pr.peek_bilibili() == "failed"
    assert pr.has_cached_readiness() is True


async def test_chat_ready_false_when_provider_unhealthy() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=False)))
    assert await pr.chat_ready() is False


async def test_chat_ready_false_when_no_registry() -> None:
    pr = InitPrereqs(_ctx(provider=None))
    assert await pr.chat_ready() is False


class _RaisingProvider:
    """Provider whose health_check raises a classifiable outage exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def health_check(self) -> bool:
        self.calls += 1
        raise self._exc


async def test_chat_detail_empty_when_ready() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True)))
    assert await pr.chat_ready() is True
    assert pr.peek_chat_detail() == ""


async def test_chat_detail_classifies_invalid_api_key() -> None:
    pr = InitPrereqs(_ctx(provider=_RaisingProvider(RuntimeError("HTTP 401 Unauthorized"))))
    assert await pr.chat_ready() is False
    detail = pr.peek_chat_detail()
    assert "401" in detail
    assert "key" in detail.lower()


async def test_chat_detail_classifies_service_unreachable() -> None:
    pr = InitPrereqs(_ctx(provider=_RaisingProvider(ConnectionError("connection refused"))))
    assert await pr.chat_ready() is False
    assert "无法连接" in pr.peek_chat_detail()


async def test_chat_detail_classifies_missing_model() -> None:
    pr = InitPrereqs(
        _ctx(provider=_RaisingProvider(RuntimeError("model 'bge' not found, try pulling it first")))
    )
    assert await pr.chat_ready() is False
    assert "模型" in pr.peek_chat_detail()


async def test_chat_detail_prefers_primary_cause_over_fallback() -> None:
    """When both providers fail, the primary's classified cause is surfaced."""
    default = _RaisingProvider(RuntimeError("HTTP 401 Unauthorized"))
    fallback = _RaisingProvider(ConnectionError("connection refused"))
    pr = InitPrereqs(_ctx_with_fallback(default, fallback))
    assert await pr.chat_ready() is False
    assert "401" in pr.peek_chat_detail()


def _ctx_with_fallback(default: Any, fallback: Any, *, fallback_name: str = "claude") -> Any:
    """Context whose registry carries a chat-capable fallback provider."""
    ctx = _ctx(provider=default)
    providers = {"": default, "openai": default}
    if fallback_name != "openai":
        providers[fallback_name] = fallback
    ctx.llm_registry = SimpleNamespace(
        get=lambda name="": providers[name],
        default_provider="openai",
        fallback_provider=fallback_name,
        is_chat_capable=lambda name: name in {"openai", fallback_name},
    )
    return ctx


async def test_chat_ready_true_via_fallback_when_default_is_down() -> None:
    """A healthy [llm].fallback_provider serves every runtime chat call, so
    init must not be blocked just because the primary provider is down."""
    default, fallback = _Provider(ok=False), _Provider(ok=True)
    pr = InitPrereqs(_ctx_with_fallback(default, fallback))
    assert await pr.chat_ready() is True
    assert default.calls == 1
    assert fallback.calls == 1


async def test_chat_ready_false_when_default_and_fallback_are_both_down() -> None:
    default, fallback = _Provider(ok=False), _Provider(ok=False)
    pr = InitPrereqs(_ctx_with_fallback(default, fallback))
    assert await pr.chat_ready() is False
    assert fallback.calls == 1


async def test_chat_ready_skips_fallback_probe_when_same_as_default() -> None:
    """A same-name fallback is dead config (the chain drops it) — probing it
    again would just double the cost of a failing default probe."""
    default, fallback = _Provider(ok=False), _Provider(ok=True)
    pr = InitPrereqs(_ctx_with_fallback(default, fallback, fallback_name="openai"))
    assert await pr.chat_ready() is False
    assert fallback.calls == 0


async def test_chat_ready_skips_fallback_probe_when_default_is_healthy() -> None:
    default, fallback = _Provider(ok=True), _Provider(ok=True)
    pr = InitPrereqs(_ctx_with_fallback(default, fallback))
    assert await pr.chat_ready() is True
    assert fallback.calls == 0


async def test_chat_ready_never_probes_non_chat_default() -> None:
    provider = _Provider(ok=True)
    registry = SimpleNamespace(
        get=lambda _name="": provider,
        default_provider="ollama",
        fallback_provider="",
        is_chat_capable=lambda _name: False,
    )
    ctx = _ctx(provider=provider)
    ctx.llm_registry = registry

    assert await InitPrereqs(ctx).chat_ready() is False
    assert provider.calls == 0


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
    monkeypatch.setattr(init_prereqs, "BILI_PROBE_TIMEOUT_SECONDS", 0.01)
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


class _RecordingAuth:
    """Fake AuthManager that records the cookie handed to validate_cookie."""

    seen: dict[str, str] = {}

    def __init__(self, **_: Any) -> None: ...

    async def validate_cookie(self, cookie: str) -> Any:
        _RecordingAuth.seen["cookie"] = cookie
        return SimpleNamespace(authenticated=True, message="", network_error=False)


async def test_bilibili_check_resolves_cli_only_login(tmp_path: Any, monkeypatch: Any) -> None:
    """Spec D3 regression: CLI ``auth login`` writes the file, not config.toml.

    Before the fix this probe read config alone, so a CLI login left
    /api/init-status reporting "not logged in" while /api/sources/status
    reported "ready" for the very same credential.
    """
    (tmp_path / "bilibili_cookie.json").write_text(
        '{"cookie": "SESSDATA=s; bili_jct=j; DedeUserID=d"}', encoding="utf-8"
    )
    _RecordingAuth.seen = {}
    monkeypatch.setattr(init_prereqs, "AuthManager", _RecordingAuth)

    ctx = _ctx(provider=_Provider(ok=True), cookie="")  # nothing in config.toml
    ctx.config.data_path = tmp_path  # but the runtime store has it
    pr = InitPrereqs(ctx)

    assert await pr.bilibili_check() == "ok"
    assert "SESSDATA=s" in _RecordingAuth.seen["cookie"]


async def test_bilibili_check_prefers_config_over_file(tmp_path: Any, monkeypatch: Any) -> None:
    """resolve_runtime_cookie semantics: config.toml wins when both are set."""
    (tmp_path / "bilibili_cookie.json").write_text(
        '{"cookie": "SESSDATA=from_file"}', encoding="utf-8"
    )
    _RecordingAuth.seen = {}
    monkeypatch.setattr(init_prereqs, "AuthManager", _RecordingAuth)

    ctx = _ctx(provider=_Provider(ok=True), cookie="SESSDATA=from_config")
    ctx.config.data_path = tmp_path
    pr = InitPrereqs(ctx)

    assert await pr.bilibili_check() == "ok"
    assert _RecordingAuth.seen["cookie"] == "SESSDATA=from_config"


async def test_bilibili_check_still_fails_when_neither_source_has_cookie(tmp_path: Any) -> None:
    """The fix must not turn an genuinely empty store into a false positive."""
    ctx = _ctx(provider=_Provider(ok=True), cookie="")
    ctx.config.data_path = tmp_path  # empty dir, no cookie file
    pr = InitPrereqs(ctx)

    assert await pr.bilibili_check() == "failed"
    assert "还没有收到" in pr.peek_bilibili_detail()
