"""Tests for the XiaohongshuAdapter (extension-driven stub).

The adapter is a no-op stub — content enters the pool via the extension's
API endpoints, not via adapter.fetch(). These tests verify it satisfies
the SourceAdapter protocol and always returns empty.
"""

from __future__ import annotations

import pytest

from openbiliclaw.sources.protocol import SourceRecipe
from openbiliclaw.sources.xiaohongshu_adapter import XiaohongshuAdapter


class TestSourceType:
    def test_source_type_is_xiaohongshu(self) -> None:
        adapter = XiaohongshuAdapter()
        assert adapter.source_type == "xiaohongshu"


class TestFetchAlwaysReturnsEmpty:
    @pytest.mark.asyncio
    async def test_fetch_returns_empty_list(self) -> None:
        adapter = XiaohongshuAdapter()
        recipe = SourceRecipe(
            id="r1",
            source_type="xiaohongshu",
            name="xhs",
            strategy="enrich",
            config={"urls": ["https://www.xiaohongshu.com/explore/abc"]},
        )
        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_for_search_strategy(self) -> None:
        adapter = XiaohongshuAdapter()
        recipe = SourceRecipe(
            id="r2",
            source_type="xiaohongshu",
            name="search",
            strategy="search",
            config={"query": "机械键盘"},
        )
        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]
        assert items == []
