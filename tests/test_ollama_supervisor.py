"""Tests for shared Ollama runtime supervision helpers."""

import httpx
import pytest

from openbiliclaw.config import Config


def test_ollama_required_detects_chat_and_embedding_routes() -> None:
    from openbiliclaw.runtime.ollama_supervisor import ollama_required

    cfg = Config()
    assert ollama_required(cfg) is False

    cfg.llm.default_provider = "ollama"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.fallback_provider = " ollama "
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.discovery.provider = "OLLAMA"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.embedding.provider = "ollama"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.embedding.fallback_provider = "ollama"
    assert ollama_required(cfg) is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.20:11434", False),
        ("https://ollama.example.com", False),
    ],
)
def test_is_loopback(url: str, expected: bool) -> None:
    from openbiliclaw.runtime.ollama_supervisor import is_loopback

    assert is_loopback(url) is expected


def test_effective_ollama_endpoint_strips_v1_suffix_for_chat() -> None:
    from openbiliclaw.runtime.ollama_supervisor import effective_ollama_endpoint

    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.base_url = "http://localhost:11434/v1/"

    assert effective_ollama_endpoint(cfg) == "http://localhost:11434"


def test_effective_ollama_endpoint_uses_embedding_base_url() -> None:
    from openbiliclaw.runtime.ollama_supervisor import effective_ollama_endpoint

    cfg = Config()
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.base_url = "http://127.0.0.1:11434/v1/"

    assert effective_ollama_endpoint(cfg) == "http://127.0.0.1:11434"


def test_ollama_probe_uses_root_api_version_after_v1_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime.ollama_supervisor import (
        _ollama_is_running,
        effective_ollama_endpoint,
    )

    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.base_url = "http://localhost:11434/v1"
    endpoint = effective_ollama_endpoint(cfg)
    seen_urls: list[str] = []

    class _FakeResp:
        status_code = 200

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _FakeResp:
            seen_urls.append(url)
            return _FakeResp()

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    assert _ollama_is_running(host=endpoint) is True
    assert seen_urls == ["http://localhost:11434/api/version"]


def test_cli_keeps_ollama_re_exports() -> None:
    from openbiliclaw import cli as cli_module
    from openbiliclaw.runtime import ollama_supervisor

    assert cli_module._ollama_is_running is ollama_supervisor._ollama_is_running
    assert (
        cli_module._ollama_start_serve_background
        is ollama_supervisor._ollama_start_serve_background
    )
