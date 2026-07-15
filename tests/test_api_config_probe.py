"""Legacy outbound-network probe compatibility tests.

Model probes are covered by ``test_api_model_config.py`` and are intentionally
unavailable on this legacy endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openbiliclaw.api.app import create_app
from openbiliclaw.api.models import ConfigServiceProbeIn, ConfigServiceProbeResponse
from openbiliclaw.config import Config, EmbeddingConfig, LLMConfig, LLMProviderConfig, save_config

if TYPE_CHECKING:
    from pathlib import Path


def test_legacy_probe_schema_accepts_only_network_proxy() -> None:
    payload = ConfigServiceProbeIn(
        kind="network_proxy",
        config={"network": {"mode": "direct", "proxy": ""}},
    )

    assert payload.kind == "network_proxy"
    with pytest.raises(ValidationError):
        ConfigServiceProbeIn(kind="llm", config={})


def test_config_probe_response_defaults_to_inline_error_shape() -> None:
    result = ConfigServiceProbeResponse(ok=False, kind="network_proxy")

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


@pytest.mark.parametrize("kind", ["llm", "llm_fallback", "embedding"])
def test_legacy_probe_endpoint_rejects_model_probe_kinds_without_echoing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    client, _path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())
    secret = "submitted-model-probe-secret"

    response = client.post(
        "/api/config/probe-service",
        json={"kind": kind, "config": {"llm": {"openai": {"api_key": secret}}}},
    )

    assert response.status_code == 422
    assert secret not in response.text


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
