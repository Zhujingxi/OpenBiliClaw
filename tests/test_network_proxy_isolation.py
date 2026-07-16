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
    from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, build_chat_adapter
    from openbiliclaw.model_config import ChatConnection

    provider = build_chat_adapter(
        ChatConnection(
            id="ollama-main",
            name="Ollama",
            type="ollama",
            model="llama3",
            base_url="http://127.0.0.1:11434/v1",
        ),
        AdapterRuntimeOptions(environment={}),
    )
    # OllamaProvider subclasses OpenAIProvider; an empty _proxy means its
    # AsyncOpenAI client was built without an injected proxied http_client.
    assert getattr(provider, "_proxy", "") == ""


# ── Domestic-endpoint carve-out ──────────────────────────────────────────
# Chinese LLM gateways must connect DIRECT even when an overseas proxy is set,
# else a user's proxy-for-OpenAI routes their DeepSeek / SenseNova / 通义
# requests through the ladder and they time out ("商汤请求总是超时").


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.deepseek.com", True),
        ("https://api.sensenova.cn/v1", True),
        ("https://open.bigmodel.cn/api/paas/v4", True),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", True),
        ("https://qianfan.baidubce.com/v2", True),
        ("https://ark.cn-beijing.volces.com/api/v3", True),
        ("https://api.siliconflow.cn/v1", True),
        ("api.moonshot.cn/v1", True),  # scheme-less base_url
        ("http://127.0.0.1:8317/v1", True),  # local gateway (cpa)
        ("http://localhost:11434/v1", True),
        ("http://192.168.1.50:8000/v1", True),  # LAN self-hosted vLLM
        ("https://api.openai.com/v1", False),
        ("https://openrouter.ai/api/v1", False),
        ("https://generativelanguage.googleapis.com", False),
        ("https://api.groq.com/openai/v1", False),
        ("", False),
    ],
)
def test_is_domestic_endpoint(url: str, expected: bool) -> None:
    assert network.is_domestic_endpoint(url) is expected


def _openai_compatible_provider(base_url: str) -> Any:
    from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, build_chat_adapter
    from openbiliclaw.model_config import ChatConnection, CredentialConfig

    return build_chat_adapter(
        ChatConnection(
            id="custom-main",
            name="Custom",
            type="openai_compatible",
            preset="custom",
            model="m",
            base_url=base_url,
            credential=CredentialConfig(source="inline", value="sk-x"),
            api_mode="chat_completions",
        ),
        AdapterRuntimeOptions(environment={}),
    )


def test_domestic_openai_compatible_forced_direct_under_custom_proxy() -> None:
    """SenseNova (via openai_compatible) stays direct even with a custom proxy."""
    provider = _openai_compatible_provider("https://api.sensenova.cn/v1")
    assert provider is not None
    assert getattr(provider, "_proxy", "") == ""
    assert getattr(provider, "_trust_env", None) is False


def test_overseas_openai_compatible_still_uses_custom_proxy() -> None:
    """The carve-out is scoped: a genuinely-overseas gateway keeps the proxy."""
    provider = _openai_compatible_provider("https://api.groq.com/openai/v1")
    assert provider is not None
    assert getattr(provider, "_proxy", "") == _GUARD_PROXY


def test_deepseek_provider_forced_direct_under_custom_proxy() -> None:
    from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, build_chat_adapter
    from openbiliclaw.model_config import ChatConnection, CredentialConfig

    provider = build_chat_adapter(
        ChatConnection(
            id="deepseek-main",
            name="DeepSeek",
            type="openai_compatible",
            preset="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            credential=CredentialConfig(source="inline", value="sk-x"),
            api_mode="chat_completions",
        ),
        AdapterRuntimeOptions(environment={}),
    )
    assert getattr(provider, "_proxy", "") == ""


def test_domestic_endpoint_ignores_system_mode() -> None:
    """In ``system`` mode a domestic endpoint must NOT inherit env proxies."""
    network.reset_outbound_proxy_for_tests()
    network.set_outbound_proxy("", mode="system")
    try:
        assert network.trust_env_for_endpoint("https://api.sensenova.cn/v1") is False
        assert network.trust_env_for_endpoint("https://api.openai.com/v1") is True
    finally:
        network.reset_outbound_proxy_for_tests()
