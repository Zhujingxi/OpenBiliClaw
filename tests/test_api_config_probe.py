from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.models import ConfigServiceProbeIn, ConfigServiceProbeResponse
from openbiliclaw.config import Config, EmbeddingConfig, LLMConfig, LLMProviderConfig, save_config
from openbiliclaw.llm.base import LLM_CONNECTIVITY_PROBE_MAX_TOKENS, LLMProviderError, LLMResponse

if TYPE_CHECKING:
    from pathlib import Path


def test_config_probe_models_accept_llm_request() -> None:
    payload = ConfigServiceProbeIn(
        kind="llm",
        config={"llm": {"default_provider": "openai"}},
    )

    assert payload.kind == "llm"
    assert payload.config["llm"]["default_provider"] == "openai"


def test_config_probe_response_defaults_to_inline_error_shape() -> None:
    result = ConfigServiceProbeResponse(ok=False, kind="embedding")

    assert result.provider == ""
    assert result.model == ""
    assert result.message == ""
    assert result.error == ""
    assert result.latency_ms == 0


def _client_for_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cfg: Config,
) -> tuple[TestClient, Path]:
    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app), config_path


def _probe_base_config() -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-old", model="gpt-old"),
            deepseek=LLMProviderConfig(api_key="sk-new", model="deepseek-chat"),
            embedding=EmbeddingConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-embedding-old",
            ),
        )
    )


def test_probe_llm_applies_unsaved_provider_payload_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str | None, dict[str, Any]]] = []

    class FakeAdapter:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        async def complete(
            self,
            messages: list[dict[str, str]],  # noqa: ARG002
            **kwargs: Any,
        ) -> LLMResponse:
            calls.append((self.connection.type, self.connection.model, kwargs))
            return LLMResponse(
                content="OK",
                provider=self.connection.type,
                model=self.connection.model,
            )

    monkeypatch.setattr(
        "openbiliclaw.llm.connection_factory.build_chat_adapter",
        lambda connection, _options: FakeAdapter(connection),
    )
    client, config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())
    before = config_path.read_bytes()

    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm",
            "config": {
                "llm": {
                    "default_provider": "deepseek",
                    "deepseek": {"api_key": "sk-new", "model": "deepseek-chat"},
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["provider"] == "openai_compatible"
    assert body["model"] == "deepseek-chat"
    assert [(provider, model) for provider, model, _kwargs in calls] == [
        ("openai_compatible", "deepseek-chat")
    ]
    assert calls[0][2]["max_tokens"] == LLM_CONNECTIVITY_PROBE_MAX_TOKENS
    assert config_path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()


def test_probe_llm_returns_inline_failure_for_unregistered_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.connection_factory.build_chat_adapter",
        lambda _connection, _options: (_ for _ in ()).throw(
            LLMProviderError("Connection type is not registered.")
        ),
    )
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "llm", "config": {"llm": {"default_provider": "deepseek"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["provider"] == "openai_compatible"
    assert "not registered" in body["error"]


def test_probe_llm_returns_inline_failure_when_provider_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingAdapter:
        async def complete(self, *_args: object, **_kwargs: object) -> LLMResponse:
            raise LLMProviderError("bad key")

    monkeypatch.setattr(
        "openbiliclaw.llm.connection_factory.build_chat_adapter",
        lambda _connection, _options: FailingAdapter(),
    )
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "llm", "config": {"llm": {"default_provider": "deepseek"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "bad key" in body["error"]


def test_probe_embedding_returns_success_when_service_probe_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEmbeddingService:
        async def probe(self) -> bool:
            return True

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_ordered_embedding_service",
        lambda route_config, *, revision, runtime_options: FakeEmbeddingService(),
    )
    client, config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())
    before = config_path.read_bytes()

    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "embedding",
            "config": {
                "llm": {
                    "embedding": {
                        "provider": "openai",
                        "api_key": "sk-embedding-new",
                        "model": "text-embedding-3-small",
                    }
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["kind"] == "embedding"
    assert body["provider"] == "openai_compatible"
    assert config_path.read_bytes() == before


def test_probe_embedding_returns_failure_when_provider_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "embedding", "config": {"llm": {"embedding": {"provider": ""}}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not configured" in body["error"].lower()


def test_probe_embedding_returns_failure_when_service_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEmbeddingService:
        async def probe(self) -> bool:
            return False

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_ordered_embedding_service",
        lambda route_config, *, revision, runtime_options: FakeEmbeddingService(),
    )
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "embedding", "config": {"llm": {"embedding": {"provider": "openai"}}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "no vector" in body["error"].lower()


# ── network_proxy probe ─────────────────────────────────────────────────────


class _FakeProxyResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeProxyClient:
    """Async context manager standing in for httpx.AsyncClient in probe tests."""

    def __init__(self, behavior: object, recorder: dict[str, Any]) -> None:
        self._behavior = behavior
        self._recorder = recorder

    async def __aenter__(self) -> _FakeProxyClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(self, url: str) -> _FakeProxyResponse:
        self._recorder["url"] = url
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return _FakeProxyResponse(int(self._behavior))


def _patch_proxy_client(monkeypatch: pytest.MonkeyPatch, behavior: object) -> dict[str, Any]:
    import httpx

    recorder: dict[str, Any] = {}

    def _factory(**kwargs: Any) -> _FakeProxyClient:
        recorder.update(kwargs)
        return _FakeProxyClient(behavior, recorder)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return recorder


def test_probe_network_proxy_ok_on_204(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder = _patch_proxy_client(monkeypatch, 204)
    client, _path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "network_proxy", "config": {"network": {"proxy": "socks5://127.0.0.1:1080"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["kind"] == "network_proxy"
    # Probe must use the candidate proxy and never inherit process env.
    assert recorder["proxy"] == "socks5://127.0.0.1:1080"
    assert recorder["trust_env"] is False


@pytest.mark.parametrize(
    ("mode", "trust_env"),
    [("direct", False), ("system", True)],
)
def test_probe_network_mode_uses_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    trust_env: bool,
) -> None:
    recorder = _patch_proxy_client(monkeypatch, 204)
    client, _path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "network_proxy", "config": {"network": {"mode": mode, "proxy": ""}}},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert recorder["trust_env"] is trust_env
    assert "proxy" not in recorder


def test_probe_network_proxy_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx

    _patch_proxy_client(monkeypatch, httpx.ConnectError("refused"))
    client, _path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "network_proxy", "config": {"network": {"proxy": "socks5://127.0.0.1:1080"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "proxy_unreachable"


def test_probe_network_proxy_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx

    _patch_proxy_client(monkeypatch, httpx.ConnectTimeout("slow"))
    client, _path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "network_proxy", "config": {"network": {"proxy": "http://127.0.0.1:7890"}}},
    )

    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "proxy_unreachable"


def test_probe_network_proxy_rejects_invalid_scheme(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "network_proxy", "config": {"network": {"proxy": "ftp://127.0.0.1:1"}}},
    )

    assert response.status_code == 400
