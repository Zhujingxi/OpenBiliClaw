"""[network].proxy long-tail mounts: yt-dlp, Codex OAuth, GitHub updater.

Each overseas client must route through the outbound proxy when set and stay
byte-equivalent to its pre-feature construction when unset (zero drift).
"""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw import network

_PROXY = "socks5://127.0.0.1:1080"


@pytest.fixture(autouse=True)
def _reset_proxy() -> Any:
    network.reset_outbound_proxy_for_tests()
    yield
    network.reset_outbound_proxy_for_tests()


# ── yt-dlp ──────────────────────────────────────────────────────────────────


def test_ytdlp_options_includes_proxy_when_set() -> None:
    from openbiliclaw.youtube.client import _ytdlp_options

    network.set_outbound_proxy(_PROXY)
    options = _ytdlp_options()
    assert options["proxy"] == _PROXY
    # Base options preserved and extra kwargs still merge.
    assert options["extract_flat"] is True
    assert _ytdlp_options(playlistend=7)["playlistend"] == 7


def test_ytdlp_options_forces_direct_when_mode_is_direct() -> None:
    from openbiliclaw.youtube.client import _ytdlp_options

    assert _ytdlp_options()["proxy"] == ""


def test_ytdlp_options_omits_proxy_only_in_system_mode() -> None:
    from openbiliclaw.youtube.client import _ytdlp_options

    network.set_outbound_proxy("", mode="system")
    assert "proxy" not in _ytdlp_options()


def test_scrapetube_search_receives_direct_proxy_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import types

    from openbiliclaw.youtube import client as yt_client

    captured: dict[str, Any] = {}
    fake = types.ModuleType("scrapetube")

    def _search(*_args: object, **kwargs: Any) -> list[dict[str, str]]:
        captured.update(kwargs)
        return [{"videoId": "v1"}]

    fake.get_search = _search  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scrapetube", fake)

    assert yt_client._scrapetube_search("test", 1) == [{"videoId": "v1"}]
    assert captured["proxies"] == {"http": "", "https": ""}


def test_scrapetube_channel_receives_custom_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import types

    from openbiliclaw.youtube import client as yt_client

    captured: dict[str, Any] = {}
    fake = types.ModuleType("scrapetube")

    def _channel(*_args: object, **kwargs: Any) -> list[dict[str, str]]:
        captured.update(kwargs)
        return [{"videoId": "v2"}]

    fake.get_channel = _channel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scrapetube", fake)
    network.set_outbound_proxy(_PROXY, mode="custom")

    assert yt_client._scrapetube_channel("UC123", 1) == [{"videoId": "v2"}]
    assert captured["proxies"] == {"http": _PROXY, "https": _PROXY}


# ── Codex OAuth token refresh ───────────────────────────────────────────────


class _StubError(Exception):
    pass


class _RecordingClient:
    def __init__(self, recorder: dict[str, Any], **kwargs: Any) -> None:
        recorder.update(kwargs)

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, *_a: object, **_kw: object) -> Any:
        raise _StubError

    async def get(self, *_a: object, **_kw: object) -> Any:
        raise _StubError


async def test_codex_refresh_uses_proxy_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.llm import codex_auth

    recorder: dict[str, Any] = {}
    monkeypatch.setattr(
        codex_auth.httpx,
        "AsyncClient",
        lambda **kw: _RecordingClient(recorder, **kw),
    )
    network.set_outbound_proxy(_PROXY)

    creds = codex_auth.CodexCredentials("access", "refresh", 9999999999)
    with pytest.raises(_StubError):
        await codex_auth.refresh_codex_token(creds)

    assert recorder.get("proxy") == _PROXY


async def test_codex_refresh_no_proxy_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.llm import codex_auth

    recorder: dict[str, Any] = {}
    monkeypatch.setattr(
        codex_auth.httpx,
        "AsyncClient",
        lambda **kw: _RecordingClient(recorder, **kw),
    )

    creds = codex_auth.CodexCredentials("access", "refresh", 9999999999)
    with pytest.raises(_StubError):
        await codex_auth.refresh_codex_token(creds)

    assert "proxy" not in recorder
    assert recorder["trust_env"] is False


async def test_codex_refresh_inherits_environment_only_in_system_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import codex_auth

    recorder: dict[str, Any] = {}
    monkeypatch.setattr(
        codex_auth.httpx,
        "AsyncClient",
        lambda **kw: _RecordingClient(recorder, **kw),
    )
    network.set_outbound_proxy("", mode="system")

    creds = codex_auth.CodexCredentials("access", "refresh", 9999999999)
    with pytest.raises(_StubError):
        await codex_auth.refresh_codex_token(creds)

    assert recorder["trust_env"] is True


# ── GitHub updater tag check ────────────────────────────────────────────────


async def test_updater_tag_fetch_uses_proxy_and_keeps_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import updater as updater_mod

    recorder: dict[str, Any] = {}
    monkeypatch.setattr(
        updater_mod.httpx,
        "AsyncClient",
        lambda **kw: _RecordingClient(recorder, **kw),
    )
    network.set_outbound_proxy(_PROXY)

    inst = updater_mod.AutoUpdateService.__new__(updater_mod.AutoUpdateService)
    # The client is constructed (recording its kwargs) before the .get call,
    # whose failure the method catches and turns into an error selection.
    await inst._fetch_latest_candidate_once(channel="backend", verify_tls=False)

    assert recorder.get("proxy") == _PROXY
    # Proxy support must coexist with the TLS-verify toggle.
    assert recorder.get("verify") is False
