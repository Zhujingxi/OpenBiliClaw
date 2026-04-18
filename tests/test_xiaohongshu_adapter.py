"""Tests for the HTTP-sidecar XiaohongshuAdapter.

The adapter forwards note URLs to the GPL-isolated xhs-downloader sidecar
over HTTP and maps the sidecar's response back to DiscoveredContent. The
main backend must never import xhs-downloader code — only the sidecar does.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from openbiliclaw.sources.protocol import SourceRecipe
from openbiliclaw.sources.xiaohongshu_adapter import XiaohongshuAdapter


def _make_recipe(urls: list[str]) -> SourceRecipe:
    return SourceRecipe(
        id="r1",
        source_type="xiaohongshu",
        name="小红书-enrich",
        strategy="enrich",
        config={"urls": urls},
    )


def _fake_transport(
    handler: Any,
) -> httpx.MockTransport:
    """Return a httpx.MockTransport routing requests through ``handler``."""
    return httpx.MockTransport(handler)


class TestSourceType:
    def test_source_type_is_xiaohongshu(self) -> None:
        adapter = XiaohongshuAdapter(sidecar_url="http://xhs-sidecar:5556")
        assert adapter.source_type == "xiaohongshu"


class TestEnrichStrategy:
    @pytest.mark.asyncio
    async def test_enriches_each_url_via_sidecar(self) -> None:
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == "/xhs/detail"
            body = request.content.decode("utf-8")
            calls.append({"body": body, "url": str(request.url)})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {
                        "作品标题": "Filco用了五六年，手痒试了试国产机械键盘",
                        "作者昵称": "键圈老用户",
                        "作品链接": "https://www.xiaohongshu.com/explore/abc123def456",
                        "作品描述": "试了一下国产红轴，感觉还不错。",
                        "作品标签": ["机械键盘", "键盘"],
                    },
                },
            )

        client = httpx.AsyncClient(transport=_fake_transport(handler))
        adapter = XiaohongshuAdapter(
            sidecar_url="http://xhs-sidecar:5556",
            client=client,
        )

        recipe = _make_recipe(
            [
                "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=XXX",
            ]
        )

        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]

        assert len(calls) == 1
        assert len(items) == 1
        item = items[0]
        assert item.source_platform == "xiaohongshu"
        assert item.title == "Filco用了五六年，手痒试了试国产机械键盘"
        assert item.up_name == "键圈老用户"
        assert item.author_name == "键圈老用户"
        assert item.content_url == "https://www.xiaohongshu.com/explore/abc123def456"
        assert item.content_id == "abc123def456"
        assert "机械键盘" in item.tags

        await client.aclose()

    @pytest.mark.asyncio
    async def test_malformed_sidecar_response_is_skipped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "error": "no_data"})

        client = httpx.AsyncClient(transport=_fake_transport(handler))
        adapter = XiaohongshuAdapter(
            sidecar_url="http://xhs-sidecar:5556",
            client=client,
        )

        recipe = _make_recipe(["https://www.xiaohongshu.com/explore/bad"])
        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]

        assert items == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_one_failing_url_does_not_fail_batch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode("utf-8")
            if "bad" in body:
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {
                        "作品标题": "good note",
                        "作者昵称": "author",
                        "作品链接": "https://www.xiaohongshu.com/explore/good123",
                    },
                },
            )

        client = httpx.AsyncClient(transport=_fake_transport(handler))
        adapter = XiaohongshuAdapter(
            sidecar_url="http://xhs-sidecar:5556",
            client=client,
        )

        recipe = _make_recipe(
            [
                "https://www.xiaohongshu.com/explore/bad",
                "https://www.xiaohongshu.com/explore/good123",
            ]
        )
        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]

        assert len(items) == 1
        assert items[0].title == "good note"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_respects_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {
                        "作品标题": "note",
                        "作者昵称": "x",
                        "作品链接": "https://www.xiaohongshu.com/explore/abc",
                    },
                },
            )

        client = httpx.AsyncClient(transport=_fake_transport(handler))
        adapter = XiaohongshuAdapter(
            sidecar_url="http://xhs-sidecar:5556",
            client=client,
        )

        urls = [f"https://www.xiaohongshu.com/explore/{i}" for i in range(10)]
        items = await adapter.fetch(_make_recipe(urls), profile=None, limit=3)  # type: ignore[arg-type]

        assert len(items) == 3
        await client.aclose()


class TestUnknownStrategy:
    @pytest.mark.asyncio
    async def test_unknown_strategy_returns_empty(self) -> None:
        adapter = XiaohongshuAdapter(sidecar_url="http://xhs-sidecar:5556")
        recipe = SourceRecipe(
            id="r9",
            source_type="xiaohongshu",
            name="search",
            strategy="search",
            config={"query": "机械键盘"},
        )
        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]
        assert items == []


class TestNoSidecarConfigured:
    @pytest.mark.asyncio
    async def test_missing_sidecar_url_returns_empty(self) -> None:
        adapter = XiaohongshuAdapter(sidecar_url=None)
        recipe = _make_recipe(["https://www.xiaohongshu.com/explore/abc"])
        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]
        assert items == []
