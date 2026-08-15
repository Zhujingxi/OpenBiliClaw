"""Allowlisted image proxy validation, fetching, and unauthenticated route access."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.hosts.api import HostDependencies, HostSecurityPolicy, create_app
from openbiliclaw.hosts.api.media_proxy import MediaProxy, MediaProxyError
from openbiliclaw.infrastructure.http.clients import HttpClientFactory
from openbiliclaw.infrastructure.http.policy import HttpPolicy, RetryPolicy
from tests.hosts.test_api import Facade

if TYPE_CHECKING:
    from pathlib import Path


def manifest(*, hosts: tuple[str, ...] = ("hdslb.com",)) -> ProviderManifest:
    return ProviderManifest(
        provider_id=ProviderId(value="demo"),
        display_name="Demo",
        capabilities=frozenset(),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
        ),
        image_hosts=hosts,
        image_headers={"referer": "https://www.bilibili.com"},
        availability=ProviderAvailability.AVAILABLE,
    )


def proxy(handler: httpx.MockTransport) -> MediaProxy:
    factory = HttpClientFactory(
        HttpPolicy(timeout_seconds=2, retry=RetryPolicy(max_attempts=1, backoff_seconds=0))
    )
    return MediaProxy((manifest(),), factory, transport=handler)


@pytest.mark.parametrize(
    "url",
    (
        "http://i0.hdslb.com/image.jpg",
        "https://i0.hdslb.com.evil.test/image.jpg",
        "https://user@i0.hdslb.com/image.jpg",
        "https://127.0.0.1/image.jpg",
        "https://[::1]/image.jpg",
        "https://i0.hdslb.com./image.jpg",
    ),
)
async def test_proxy_rejects_non_https_and_host_bypass_variants(url: str) -> None:
    media = proxy(httpx.MockTransport(lambda _request: httpx.Response(500)))
    with pytest.raises(MediaProxyError) as caught:
        await media.fetch(url)
    assert caught.value.status_code == 404


async def test_proxy_accepts_allowed_subdomain_and_forwards_provider_headers() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "i0.hdslb.com"
        assert request.headers["referer"] == "https://www.bilibili.com"
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpeg")

    result = await proxy(httpx.MockTransport(handle)).fetch("https://I0.HDSLB.COM/pic/image.jpg")
    assert result.content == b"jpeg"
    assert result.content_type == "image/jpeg"


async def test_proxy_rejects_non_image_and_oversized_responses() -> None:
    non_image = proxy(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"not image"
            )
        )
    )
    with pytest.raises(MediaProxyError) as wrong_type:
        await non_image.fetch("https://i0.hdslb.com/a")
    assert wrong_type.value.status_code == 502

    oversized = proxy(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"x" * (10 * 1024 * 1024 + 1),
            )
        )
    )
    with pytest.raises(MediaProxyError) as too_large:
        await oversized.fetch("https://i0.hdslb.com/b")
    assert too_large.value.status_code == 502


async def test_proxy_streaming_cap_applies_without_content_length() -> None:
    response = httpx.Response(
        200, headers={"content-type": "image/png"}, content=b"x" * (10 * 1024 * 1024 + 1)
    )
    response.headers.pop("content-length", None)
    media = proxy(httpx.MockTransport(lambda _request: response))
    with pytest.raises(MediaProxyError) as caught:
        await media.fetch("https://i0.hdslb.com/streamed")
    assert caught.value.status_code == 502


async def test_proxy_maps_upstream_failures_and_never_follows_redirects() -> None:
    not_found = proxy(httpx.MockTransport(lambda _request: httpx.Response(404)))
    with pytest.raises(MediaProxyError) as upstream_404:
        await not_found.fetch("https://i0.hdslb.com/missing")
    assert upstream_404.value.status_code == 404

    redirect = proxy(
        httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "https://evil.test/track"})
        )
    )
    with pytest.raises(MediaProxyError) as redirected:
        await redirect.fetch("https://i0.hdslb.com/moved")
    assert redirected.value.status_code == 502


@pytest.mark.parametrize("url", ("https://i0.hdslb.com:8080/image.jpg", "not a url"))
async def test_proxy_rejects_non_default_ports_and_malformed_urls(url: str) -> None:
    media = proxy(httpx.MockTransport(lambda _request: httpx.Response(500)))
    with pytest.raises(MediaProxyError) as caught:
        await media.fetch(url)
    assert caught.value.status_code == 404


async def test_media_route_is_unauthenticated_but_still_allowlisted(tmp_path: Path) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("not-image"):
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"no")
        return httpx.Response(200, headers={"content-type": "image/webp"}, content=b"webp")

    dependencies = HostDependencies(
        facade=Facade(),
        security=HostSecurityPolicy(bearer_token="secret"),
        media_proxy=proxy(httpx.MockTransport(handle)),
    )
    app = create_app(dependencies, frontend_dir=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        allowed = await client.get("/v1/media", params={"url": "https://i0.hdslb.com/a.webp"})
        rejected = await client.get("/v1/media", params={"url": "https://evil.test/a.webp"})
        bad_upstream = await client.get(
            "/v1/media", params={"url": "https://i0.hdslb.com/not-image"}
        )
        protected = await client.get("/v1/sources")
    assert allowed.status_code == 200
    assert allowed.content == b"webp"
    assert allowed.headers["content-type"] == "image/webp"
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "not_found"
    assert bad_upstream.status_code == 502
    assert bad_upstream.json()["error"]["code"] == "temporary_failure"
    assert protected.status_code == 401
