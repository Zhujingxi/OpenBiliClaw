from __future__ import annotations

import os
from pathlib import Path

import pytest

from openbiliclaw.ai.providers.catalog import (
    CATALOG_TTL_SECONDS,
    CapabilityConfig,
    CatalogError,
    ModelCatalog,
    ProviderCatalog,
    UnsupportedProtocolError,
    protocol_for,
    resolve_model,
)

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "models.dev.small.json"


def fixture_catalog() -> ProviderCatalog:
    return ProviderCatalog.model_validate_json(_FIXTURE.read_bytes())


def capabilities() -> CapabilityConfig:
    return CapabilityConfig(
        tools=True,
        structured_output=True,
        vision=False,
        context_tokens=123,
        streaming=False,
        reasoning=False,
    )


def test_catalog_fetches_caches_and_uses_fresh_cache(tmp_path: Path) -> None:
    calls = 0

    def fetch(_url: str) -> bytes:
        nonlocal calls
        calls += 1
        return _FIXTURE.read_bytes()

    cache = tmp_path / "models.dev.json"
    service = ModelCatalog(cache, fetch=fetch)
    assert "deepseek" in service.load(now=100).root
    os.utime(cache, (100, 100))
    assert "kimi-for-coding" in service.load(now=100 + CATALOG_TTL_SECONDS - 1).root
    assert calls == 1


def test_corrupt_cache_is_replaced_by_healthy_fetch(tmp_path: Path) -> None:
    cache = tmp_path / "models.dev.json"
    cache.write_bytes(b"not-json")

    catalog = ModelCatalog(cache, fetch=lambda _url: _FIXTURE.read_bytes()).load(now=100)

    assert "deepseek" in catalog.root
    assert ProviderCatalog.model_validate_json(cache.read_bytes()).root == catalog.root


def test_stale_cache_is_offline_fallback_and_missing_cache_is_typed_error(
    tmp_path: Path,
) -> None:
    def unavailable(_url: str) -> bytes:
        raise OSError("offline")

    cache = tmp_path / "models.dev.json"
    cache.write_bytes(_FIXTURE.read_bytes())
    os.utime(cache, (1, 1))
    assert "openai" in ModelCatalog(cache, fetch=unavailable).load(now=100_000).root
    with pytest.raises(CatalogError, match="no cache exists"):
        ModelCatalog(tmp_path / "missing.json", fetch=unavailable).load(now=100_000)


def test_catalog_resolves_protocol_endpoint_and_capabilities() -> None:
    catalog = fixture_catalog()
    kimi = resolve_model(
        catalog,
        provider_id="kimi-for-coding",
        model_name="kimi-for-coding",
        endpoint=None,
        protocol=None,
        capabilities=None,
    )
    assert kimi.protocol == "anthropic"
    assert kimi.endpoint == "https://api.kimi.com/coding/v1"
    assert kimi.capabilities.tools
    assert kimi.capabilities.streaming
    assert kimi.capabilities.reasoning

    deepseek = resolve_model(
        catalog,
        provider_id="deepseek",
        model_name="deepseek-chat",
        endpoint=None,
        protocol=None,
        capabilities=None,
    )
    assert deepseek.protocol == "openai"
    assert deepseek.endpoint == "https://api.deepseek.com"


def test_custom_provider_requires_and_accepts_complete_explicit_shape() -> None:
    catalog = fixture_catalog()
    with pytest.raises(CatalogError, match="requires protocol, endpoint, and capabilities"):
        resolve_model(
            catalog,
            provider_id="private",
            model_name="model",
            endpoint="https://private.example/v1",
            protocol="openai",
            capabilities=None,
        )
    resolved = resolve_model(
        catalog,
        provider_id="private",
        model_name="model",
        endpoint="https://private.example/v1",
        protocol="openai",
        capabilities=capabilities(),
    )
    assert resolved.capabilities.context_tokens == 123


def test_unknown_catalog_protocol_fails_with_marker() -> None:
    with pytest.raises(UnsupportedProtocolError, match="@vendor/unknown"):
        protocol_for("@vendor/unknown")
