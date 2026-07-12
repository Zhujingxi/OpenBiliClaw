"""CN-direct isolation guard for [network].proxy.

Pitfall rule 1: bilibili / douyin / ollama must NEVER inherit a proxy — an
exit IP trips CN risk control (df626f3f). This test pins that the overseas
outbound proxy set via ``openbiliclaw.network`` does not leak into any
CN-direct httpx client construction, and that those clients keep
``trust_env=False``.

If someone wires the outbound-proxy helper into one of these clients, this
test MUST go red. That fail-first property was verified during development by
temporarily injecting the proxy into ``BilibiliAPIClient`` and confirming this
test failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from openbiliclaw import network

if TYPE_CHECKING:
    from collections.abc import Iterator

_GUARD_PROXY = "socks5://127.0.0.1:9999"


@pytest.fixture(autouse=True)
def _proxy_set() -> Iterator[None]:
    network.reset_outbound_proxy_for_tests()
    network.set_outbound_proxy(_GUARD_PROXY)
    yield
    network.reset_outbound_proxy_for_tests()


@pytest.fixture
def _capture_httpx(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    orig_init = httpx.AsyncClient.__init__

    def _recording_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        captured.append(kwargs)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _recording_init)
    return captured


def _assert_no_proxy_leak(captured: list[dict[str, Any]]) -> None:
    assert captured, "expected at least one httpx.AsyncClient construction"
    for kwargs in captured:
        assert kwargs.get("proxy") != _GUARD_PROXY
        assert kwargs.get("proxies") != _GUARD_PROXY
        assert kwargs.get("trust_env") is False


def test_bilibili_client_never_uses_outbound_proxy(
    _capture_httpx: list[dict[str, Any]],
) -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    BilibiliAPIClient(cookie="SESSDATA=x; bili_jct=y; DedeUserID=1")
    _assert_no_proxy_leak(_capture_httpx)


def test_douyin_client_never_uses_outbound_proxy(
    _capture_httpx: list[dict[str, Any]],
) -> None:
    from openbiliclaw.sources.douyin_direct import DouyinDirectClient

    DouyinDirectClient(cookie="sessionid=abc; ttwid=def")
    _assert_no_proxy_leak(_capture_httpx)


def test_ollama_factory_provider_has_no_outbound_proxy() -> None:
    """Ollama chat provider (localhost) must not carry the outbound proxy."""
    from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig
    from openbiliclaw.llm.registry import _maybe_ollama_provider

    config = Config(llm=LLMConfig(ollama=LLMProviderConfig(model="llama3")))
    provider = _maybe_ollama_provider(config, {})
    assert provider is not None
    # OllamaProvider subclasses OpenAIProvider; an empty _proxy means its
    # AsyncOpenAI client was built without an injected proxied http_client.
    assert getattr(provider, "_proxy", "") == ""
