"""Tests for discovery cover-image preparation."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.discovery.multimodal import (
    prepare_cover_bytes_for_embedding,
    prepare_cover_image_input,
)
from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url


@pytest.mark.asyncio
async def test_prepare_cover_image_input_resizes_and_encodes_jpeg(monkeypatch) -> None:
    source = BytesIO()
    Image.new("RGB", (320, 160), color=(255, 0, 0)).save(source, format="PNG")

    async def fake_get_or_fetch_cover_bytes(url: str) -> tuple[bytes, str]:
        assert url == "https://i.ytimg.com/vi/demo/hqdefault.jpg"
        return source.getvalue(), "image/png"

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.get_or_fetch_cover_bytes",
        fake_get_or_fetch_cover_bytes,
    )

    prepared = await prepare_cover_image_input(
        content_id="yt-demo",
        cover_url="https://i.ytimg.com/vi/demo/hqdefault.jpg",
        max_px=64,
        quality=60,
        timeout_seconds=1,
    )

    assert prepared is not None
    assert prepared.content_id == "yt-demo"
    assert prepared.mime_type == "image/jpeg"
    assert prepared.data_url.startswith("data:image/jpeg;base64,")

    encoded = prepared.data_url.split(",", 1)[1]
    image = Image.open(BytesIO(base64.b64decode(encoded)))
    assert max(image.size) <= 64


@pytest.mark.asyncio
async def test_prepare_cover_bytes_for_embedding_returns_jpeg(monkeypatch) -> None:
    source = BytesIO()
    Image.new("RGB", (200, 100), color=(0, 128, 255)).save(source, format="PNG")

    async def fake_get_or_fetch_cover_bytes(url: str) -> tuple[bytes, str]:
        return source.getvalue(), "image/png"

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.get_or_fetch_cover_bytes",
        fake_get_or_fetch_cover_bytes,
    )

    prepared = await prepare_cover_bytes_for_embedding(
        "https://i0.hdslb.com/bfs/archive/demo.png",
        max_px=64,
        quality=60,
        timeout_seconds=1,
    )

    assert prepared is not None
    image_bytes, mime_type = prepared
    assert mime_type == "image/jpeg"
    image = Image.open(BytesIO(image_bytes))
    assert max(image.size) <= 64


@pytest.mark.asyncio
async def test_warm_cover_embeddings_uses_url_keyed_cache(monkeypatch) -> None:
    """Warmer must store the vector under the URL-derived key so the delight
    hot path can look it up by cover URL without re-fetching bytes.
    """

    class _Emb:
        multimodal_enabled = True
        supports_image_embedding = True

        def __init__(self) -> None:
            self.calls: list[tuple[int, str, str]] = []

        def image_embedding_active(self) -> bool:
            return True

        async def embed_image(
            self,
            image_bytes: bytes,
            *,
            mime_type: str = "image/jpeg",
            cache_key: str | None = None,
        ) -> list[float]:
            self.calls.append((len(image_bytes), mime_type, cache_key or ""))
            return [0.1, 0.2]

    emb = _Emb()
    engine = ContentDiscoveryEngine(embedding_service=emb)  # type: ignore[arg-type]

    async def fake_prepare(cover_url, *, max_px, quality, timeout_seconds):
        return b"jpeg-bytes", "image/jpeg"

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.prepare_cover_bytes_for_embedding",
        fake_prepare,
    )

    url = "https://example.com/c.jpg"
    items = [
        DiscoveredContent(bvid="BV1", title="t", cover_url=url),
        DiscoveredContent(bvid="BV2", title="no-cover", cover_url=""),
    ]
    await engine._warm_cover_embeddings(items)

    assert emb.calls == [(10, "image/jpeg", image_embedding_cache_key_for_url(url))]


@pytest.mark.asyncio
async def test_warm_cover_embeddings_skips_when_inactive() -> None:
    from types import SimpleNamespace

    emb = SimpleNamespace(
        image_embedding_active=lambda: False,
        embed_image=None,
    )
    engine = ContentDiscoveryEngine(embedding_service=emb)  # type: ignore[arg-type]
    items = [DiscoveredContent(bvid="BV1", title="t", cover_url="https://example.com/c.jpg")]
    # Must not raise even without a usable embed_image.
    await engine._warm_cover_embeddings(items)
