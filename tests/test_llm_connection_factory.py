"""Connection-record adapter construction tests.

All SDK request surfaces are fakes.  These tests must never contact a model
service, a local Ollama daemon, or the Codex credential store.
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from openbiliclaw.llm import gemini_provider, openai_provider
from openbiliclaw.llm.base import LLMProviderError
from openbiliclaw.model_config import (
    ChatConnection,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
)
from openbiliclaw.model_config.registry import connection_type_registry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

try:
    from openbiliclaw.llm import connection_factory as _connection_factory
except ImportError:
    _connection_factory = None


def _factory() -> Any:
    assert _connection_factory is not None, "connection factory is not implemented"
    return _connection_factory


def _runtime_options(
    *,
    environment: Mapping[str, str] | None = None,
    codex_token_loader: Callable[[], str] | None = None,
) -> Any:
    return _factory().AdapterRuntimeOptions(
        timeout_seconds=42.0,
        environment=environment,
        codex_token_loader=codex_token_loader,
    )


def _inline(secret: str = "test-secret") -> CredentialConfig:
    return CredentialConfig(source="inline", value=secret)


def _openai_connection(
    preset: str,
    *,
    connection_id: str | None = None,
    base_url: str | None = None,
    api_mode: str = "chat_completions",
    reasoning_effort: str = "",
    http_referer: str = "",
    x_title: str = "",
    credential: CredentialConfig | None = None,
) -> ChatConnection:
    defaults = {
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "custom": "https://gateway.example.test/v1",
    }
    return ChatConnection(
        id=connection_id or f"chat-{preset}",
        name=f"Friendly {preset} label",
        type="openai_compatible",
        preset=preset,
        model="model-test",
        base_url=defaults[preset] if base_url is None else base_url,
        credential=credential or _inline(),
        api_mode=api_mode,
        reasoning_effort=reasoning_effort,
        http_referer=http_referer,
        x_title=x_title,
    )


def _anthropic_connection(
    preset: str,
    *,
    base_url: str | None = None,
) -> ChatConnection:
    endpoint = (
        "https://api.anthropic.com"
        if preset == "anthropic"
        else "https://claude-gateway.example.test"
    )
    return ChatConnection(
        id=f"anthropic-{preset}",
        name="Friendly Anthropic label",
        type="anthropic_compatible",
        preset=preset,
        model="claude-test",
        base_url=endpoint if base_url is None else base_url,
        credential=_inline(),
    )


def _chat_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        model="model-test",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        ),
    )


def _responses_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        model="model-test",
        output_text=content,
        output=[],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
    )


class _FakeEndpoint:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        await asyncio.sleep(0)
        return self.response


class _FakeOpenAIClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)
        self.api_key = kwargs.get("api_key")
        self.max_retries = kwargs.get("max_retries")
        self.chat_endpoint = _FakeEndpoint(_chat_response())
        self.responses_endpoint = _FakeEndpoint(_responses_response())
        self.embedding_endpoint = _FakeEndpoint(
            SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])
        )
        self.chat = SimpleNamespace(completions=self.chat_endpoint)
        self.responses = self.responses_endpoint
        self.embeddings = self.embedding_endpoint


@pytest.fixture
def fake_openai_clients(monkeypatch: pytest.MonkeyPatch) -> list[_FakeOpenAIClient]:
    clients: list[_FakeOpenAIClient] = []

    def build_client(**kwargs: object) -> _FakeOpenAIClient:
        client = _FakeOpenAIClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(openai_provider, "AsyncOpenAI", build_client)
    monkeypatch.setattr(openai_provider.httpx, "AsyncClient", lambda **_: object())
    return clients


@pytest.mark.parametrize("preset", ["openai", "deepseek", "openrouter", "custom"])
def test_openai_presets_use_one_protocol_adapter(
    preset: str,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    adapter = _factory().build_chat_adapter(_openai_connection(preset), _runtime_options())

    assert type(adapter) is openai_provider.OpenAIProtocolProvider
    assert adapter.name == f"chat-{preset}"
    assert fake_openai_clients[-1].kwargs["base_url"] == _openai_connection(preset).base_url


def test_openai_protocol_options_are_deeply_immutable() -> None:
    options = _factory().OpenAIProtocolOptions(
        connection_id="chat-a",
        preset="openrouter",
        api_mode="chat_completions",
        extra_headers={"X-Title": "OpenBiliClaw"},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        options.preset = "deepseek"
    with pytest.raises(TypeError):
        cast("dict[str, str]", options.extra_headers)["X-Title"] = "changed"


@pytest.mark.asyncio
async def test_openai_protocol_hooks_do_not_cross_connection_boundaries(
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    deepseek = _factory().build_chat_adapter(
        _openai_connection("deepseek", reasoning_effort="max"),
        _runtime_options(),
    )
    openrouter = _factory().build_chat_adapter(
        _openai_connection(
            "openrouter",
            http_referer="https://openbiliclaw.test",
            x_title="OpenBiliClaw",
        ),
        _runtime_options(),
    )
    responses = _factory().build_chat_adapter(
        _openai_connection("openai", api_mode="responses"),
        _runtime_options(),
    )

    await asyncio.gather(
        deepseek.complete([{"role": "user", "content": "one"}], max_tokens=32),
        openrouter.complete([{"role": "user", "content": "two"}]),
        responses.complete([{"role": "user", "content": "three"}]),
    )

    deepseek_call = fake_openai_clients[0].chat_endpoint.calls[0]
    openrouter_call = fake_openai_clients[1].chat_endpoint.calls[0]
    responses_call = fake_openai_clients[2].responses_endpoint.calls[0]
    assert deepseek_call["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }
    assert deepseek_call["max_tokens"] == 32768
    assert "extra_headers" not in deepseek_call
    assert openrouter_call["extra_headers"] == {
        "HTTP-Referer": "https://openbiliclaw.test",
        "X-Title": "OpenBiliClaw",
    }
    assert "extra_body" not in openrouter_call
    assert responses_call["input"] == [{"role": "user", "content": "three"}]
    assert not fake_openai_clients[2].chat_endpoint.calls


@pytest.mark.asyncio
async def test_deepseek_explicit_empty_effort_disables_thinking(
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    adapter = _factory().build_chat_adapter(
        _openai_connection("deepseek", reasoning_effort="max"),
        _runtime_options(),
    )

    await adapter.complete(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="",
    )

    assert fake_openai_clients[0].chat_endpoint.calls[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_anthropic_official_and_custom_use_one_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory()
    anthropic_provider = __import__(
        "openbiliclaw.llm.anthropic_provider", fromlist=["AnthropicCompatibleProvider"]
    )
    sdk_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        anthropic_provider,
        "AsyncAnthropic",
        lambda **kwargs: sdk_calls.append(dict(kwargs)) or SimpleNamespace(),
    )
    monkeypatch.setattr(anthropic_provider.httpx, "AsyncClient", lambda **_: object())

    official = factory.build_chat_adapter(_anthropic_connection("anthropic"), _runtime_options())
    custom = factory.build_chat_adapter(_anthropic_connection("custom"), _runtime_options())

    assert type(official) is anthropic_provider.AnthropicCompatibleProvider
    assert type(custom) is anthropic_provider.AnthropicCompatibleProvider
    assert official.name == "anthropic-anthropic"
    assert custom.name == "anthropic-custom"
    assert sdk_calls[0]["base_url"] == "https://api.anthropic.com"
    assert sdk_calls[1]["base_url"] == "https://claude-gateway.example.test"


def test_gemini_chat_uses_native_sdk_and_connection_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        gemini_provider,
        "genai",
        SimpleNamespace(
            Client=lambda **kwargs: sdk_calls.append(dict(kwargs)) or SimpleNamespace()
        ),
    )
    connection = ChatConnection(
        id="gemini-a",
        name="Friendly Gemini label",
        type="gemini_api",
        model="gemini-test",
        base_url="https://gemini-gateway.example.test",
        credential=_inline(),
    )

    adapter = _factory().build_chat_adapter(connection, _runtime_options())

    assert type(adapter) is gemini_provider.GeminiProvider
    assert adapter.name == "gemini-a"
    assert sdk_calls[0]["api_key"] == "test-secret"
    http_options = cast("dict[str, object]", sdk_calls[0]["http_options"])
    assert http_options["base_url"] == "https://gemini-gateway.example.test/"


def test_gemini_server_error_uses_connection_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gemini_provider,
        "genai",
        SimpleNamespace(Client=lambda **_: SimpleNamespace()),
    )
    connection = ChatConnection(
        id="gemini-a",
        name="Friendly Gemini label",
        type="gemini_api",
        model="gemini-test",
        credential=_inline(),
    )
    adapter = _factory().build_chat_adapter(connection, _runtime_options())
    upstream_error = RuntimeError("upstream unavailable")
    upstream_error.status_code = 503  # type: ignore[attr-defined]

    mapped = adapter._map_error(upstream_error)

    assert str(mapped) == "gemini-a server error: 503"


@pytest.mark.asyncio
async def test_ollama_connection_passes_num_ctx_and_is_always_direct(
    monkeypatch: pytest.MonkeyPatch,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    from openbiliclaw import network
    from openbiliclaw.llm.ollama_provider import OllamaProvider

    monkeypatch.setattr(network, "proxy_for_endpoint", lambda _: "socks5://127.0.0.1:9999")
    monkeypatch.setattr(network, "trust_env_for_endpoint", lambda _: True)
    connection = ChatConnection(
        id="ollama-a",
        name="Friendly Ollama label",
        type="ollama",
        model="qwen-test",
        base_url="http://127.0.0.1:11434",
        num_ctx=8192,
    )
    adapter = _factory().build_chat_adapter(connection, _runtime_options())
    captured: dict[str, object] = {}

    async def fake_post(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {
            "model": "qwen-test",
            "message": {"content": "ok"},
            "prompt_eval_count": 1,
            "eval_count": 1,
        }

    monkeypatch.setattr(adapter, "_post_chat", fake_post)
    await adapter.complete([{"role": "user", "content": "hi"}])

    assert type(adapter) is OllamaProvider
    assert adapter.name == "ollama-a"
    assert cast("dict[str, object]", captured["options"])["num_ctx"] == 8192
    assert adapter._proxy == ""
    assert adapter._trust_env is False
    assert fake_openai_clients[0].kwargs["base_url"] == "http://127.0.0.1:11434/v1"


@pytest.mark.parametrize("source", ["inline", "env"])
def test_factory_resolves_only_the_selected_credential_source(
    source: str,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    secret = f"{source}-resolved-secret"
    credential = (
        CredentialConfig(source="inline", value=secret)
        if source == "inline"
        else CredentialConfig(source="env", value="EXACT_MODEL_KEY")
    )
    options = _runtime_options(
        environment={
            "EXACT_MODEL_KEY": secret,
            "OPENAI_API_KEY": "must-not-be-used",
        }
    )

    adapter = _factory().build_chat_adapter(
        _openai_connection("openai", credential=credential),
        options,
    )

    assert fake_openai_clients[0].kwargs["api_key"] == secret
    assert secret not in repr(adapter)
    assert secret not in repr(options)


@pytest.mark.parametrize(
    "credential",
    [
        CredentialConfig(source="inline", value=""),
        CredentialConfig(source="env", value="MISSING_MODEL_KEY"),
        CredentialConfig(source="oauth", value="unsupported-ref"),
        CredentialConfig(source="none"),
    ],
)
def test_invalid_credentials_raise_one_safe_fixed_error(
    credential: CredentialConfig,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    connection = _openai_connection("openai", credential=credential)

    with pytest.raises(LLMProviderError) as exc_info:
        _factory().build_chat_adapter(
            connection,
            _runtime_options(environment={}),
        )

    assert str(exc_info.value) == "connection credential is unavailable"
    assert connection.name not in str(exc_info.value)
    assert connection.type not in str(exc_info.value)
    assert connection.preset not in str(exc_info.value)
    if credential.value:
        assert credential.value not in str(exc_info.value)
    assert not fake_openai_clients


def test_none_credentials_are_allowed_only_by_no_credential_descriptor(
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    connection = ChatConnection(
        id="ollama-no-key",
        name="Local Ollama",
        type="ollama",
        model="llama3",
        base_url="http://127.0.0.1:11434/v1",
    )

    adapter = _factory().build_chat_adapter(connection, _runtime_options())

    definition = connection_type_registry().definition("ollama")
    assert all(field.name != "credential" for field in definition.fields)
    assert adapter.name == "ollama-no-key"
    assert fake_openai_clients[0].kwargs["api_key"] == "ollama"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.openai.com/v1",
        "https://evil.example.test/v1",
        "https://api.openai.com:443/v1",
        "https://api.openai.com/v1/chat",
        "https://api.openai.com/v1?key=value",
        "https://api.openai.com/v1#fragment",
        "https://user@api.openai.com/v1",
    ],
)
def test_codex_oauth_rejects_non_official_endpoint_before_token_lookup(
    endpoint: str,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    calls = 0

    def token_loader() -> str:
        nonlocal calls
        calls += 1
        return "oauth-secret-token"

    connection = ChatConnection(
        id="codex-a",
        name="Codex login",
        type="codex_oauth",
        model="gpt-test",
        base_url=endpoint,
        credential=CredentialConfig(source="oauth", value="codex"),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        _factory().build_chat_adapter(
            connection,
            _runtime_options(codex_token_loader=token_loader),
        )

    assert str(exc_info.value) == "connection endpoint is not allowed"
    assert calls == 0
    assert "oauth-secret-token" not in repr(connection)
    assert not fake_openai_clients


@pytest.mark.parametrize("endpoint", ["", "https://api.openai.com/v1"])
def test_codex_oauth_accepts_only_the_exact_official_endpoint(
    endpoint: str,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    token = "oauth-secret-token"
    connection = ChatConnection(
        id="codex-a",
        name="Codex login",
        type="codex_oauth",
        model="gpt-test",
        base_url=endpoint,
        credential=CredentialConfig(source="oauth", value="codex"),
    )

    adapter = _factory().build_chat_adapter(
        connection,
        _runtime_options(codex_token_loader=lambda: token),
    )

    assert type(adapter) is openai_provider.OpenAIProtocolProvider
    assert adapter.name == "codex-a"
    assert fake_openai_clients[0].kwargs["api_key"] == token
    assert fake_openai_clients[0].kwargs["base_url"] == "https://api.openai.com/v1"
    assert token not in repr(adapter)
    assert token not in repr(adapter.options)


def test_factory_resolves_endpoint_aware_proxy_policy_internally(
    monkeypatch: pytest.MonkeyPatch,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    from openbiliclaw import network

    seen: list[tuple[str, str]] = []

    def proxy_for_endpoint(endpoint: str) -> str:
        seen.append(("proxy", endpoint))
        return "socks5://127.0.0.1:9999"

    def trust_env_for_endpoint(endpoint: str) -> bool:
        seen.append(("trust_env", endpoint))
        return False

    monkeypatch.setattr(network, "proxy_for_endpoint", proxy_for_endpoint)
    monkeypatch.setattr(network, "trust_env_for_endpoint", trust_env_for_endpoint)
    adapter = _factory().build_chat_adapter(
        _openai_connection("custom", base_url="https://gateway.example.test/v1"),
        _runtime_options(),
    )

    assert adapter._proxy == "socks5://127.0.0.1:9999"
    assert adapter._trust_env is False
    assert seen == [
        ("proxy", "https://gateway.example.test/v1"),
        ("trust_env", "https://gateway.example.test/v1"),
    ]
    assert "proxy" not in dataclasses.asdict(_runtime_options())
    assert fake_openai_clients[0].kwargs["base_url"] == "https://gateway.example.test/v1"


@pytest.mark.parametrize(
    ("provider", "expected_type_name"),
    [
        (
            EmbeddingProviderConfig(
                id="embedding-openai",
                name="OpenAI embedding",
                type="openai_compatible",
                preset="openai",
                base_url="https://api.openai.com/v1",
                credential=_inline(),
            ),
            "OpenAIProtocolProvider",
        ),
        (
            EmbeddingProviderConfig(
                id="embedding-custom",
                name="Custom embedding",
                type="openai_compatible",
                preset="custom",
                base_url="https://embedding.example.test/v1",
                credential=_inline(),
            ),
            "OpenAIProtocolProvider",
        ),
        (
            EmbeddingProviderConfig(
                id="embedding-gemini",
                name="Gemini embedding",
                type="gemini_api",
                credential=_inline(),
            ),
            "GeminiProvider",
        ),
        (
            EmbeddingProviderConfig(
                id="embedding-dashscope",
                name="DashScope embedding",
                type="dashscope_api",
                credential=_inline(),
            ),
            "DashScopeEmbeddingProvider",
        ),
        (
            EmbeddingProviderConfig(
                id="embedding-ollama",
                name="Ollama embedding",
                type="ollama",
                base_url="http://127.0.0.1:11434/v1",
            ),
            "OllamaProvider",
        ),
    ],
)
@pytest.mark.asyncio
async def test_every_registry_embedding_adapter_receives_one_shared_settings_object(
    provider: EmbeddingProviderConfig,
    expected_type_name: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    sdk_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        gemini_provider,
        "genai",
        SimpleNamespace(
            Client=lambda **kwargs: sdk_calls.append(dict(kwargs)) or SimpleNamespace()
        ),
    )
    settings = EmbeddingModelSettings(
        model="shared-embedding-model",
        output_dimensionality=768,
        similarity_threshold=0.77,
        multimodal_enabled=True,
    )

    adapter = _factory().build_embedding_adapter(provider, settings, _runtime_options())

    assert adapter.name == provider.id
    assert adapter.settings is settings
    assert type(adapter.provider).__name__ == expected_type_name
    assert provider.id == adapter.name
    assert not hasattr(provider, "model")
    assert not hasattr(provider, "settings")
    definition = connection_type_registry().definition(provider.type)
    assert "embedding" in definition.capabilities

    captured_model = ""

    async def fake_embed(_: str, *, model: str) -> list[float]:
        nonlocal captured_model
        captured_model = model
        return [0.1, 0.2]

    monkeypatch.setattr(adapter.provider, "embed", fake_embed)
    assert await adapter.embed("hello") == [0.1, 0.2]
    assert captured_model == settings.model


@pytest.mark.asyncio
async def test_openai_embedding_adapter_passes_shared_output_dimension(
    fake_openai_clients: list[_FakeOpenAIClient],
) -> None:
    provider = EmbeddingProviderConfig(
        id="embedding-openai",
        name="OpenAI embedding",
        type="openai_compatible",
        preset="openai",
        base_url="https://api.openai.com/v1",
        credential=_inline(),
    )
    settings = EmbeddingModelSettings(
        model="text-embedding-3-small",
        output_dimensionality=512,
    )
    adapter = _factory().build_embedding_adapter(provider, settings, _runtime_options())

    assert await adapter.embed("hello") == [0.1, 0.2]
    assert fake_openai_clients[0].embedding_endpoint.calls == [
        {
            "model": "text-embedding-3-small",
            "input": "hello",
            "dimensions": 512,
        }
    ]


def test_unsupported_chat_and_embedding_types_raise_safe_errors() -> None:
    chat = ChatConnection(
        id="unknown-chat",
        name="Sensitive friendly label",
        type="unknown-type",
        model="unknown-model",
        credential=_inline("sensitive-secret"),
    )
    embedding = EmbeddingProviderConfig(
        id="unknown-embedding",
        name="Sensitive embedding label",
        type="anthropic_compatible",
        credential=_inline("another-sensitive-secret"),
    )

    with pytest.raises(LLMProviderError, match="^connection type is not supported$"):
        _factory().build_chat_adapter(chat, _runtime_options())
    with pytest.raises(LLMProviderError, match="^connection capability is not supported$"):
        _factory().build_embedding_adapter(
            embedding,
            EmbeddingModelSettings(model="shared-model"),
            _runtime_options(),
        )
