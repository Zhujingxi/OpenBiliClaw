"""Optional integration tests for the Bilibili API client."""

from __future__ import annotations

import os

import pytest

from openbiliclaw.bilibili.api import BilibiliAPIClient

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("OPENBILICLAW_BILIBILI_COOKIE"),
    reason="requires OPENBILICLAW_BILIBILI_COOKIE for real API access",
)
@pytest.mark.asyncio
async def test_nav_info_integration() -> None:
    """Validate local cookie wiring against the real nav endpoint."""
    client = BilibiliAPIClient(cookie=os.environ["OPENBILICLAW_BILIBILI_COOKIE"])
    try:
        nav = await client.get_nav_info()
    finally:
        await client.close()

    assert nav.is_login is True
    assert nav.uname != ""
