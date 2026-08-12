from __future__ import annotations

import json

import httpx
import pytest

from openbiliclaw.access.models import CredentialAccessHandle, Permission
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.bilibili.client import HttpxBilibiliTransport, cookie_parts


def _handle() -> CredentialAccessHandle:
    return CredentialAccessHandle(
        provider_id="bilibili",
        account_id="42",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )


def test_cookie_parser_requires_exact_names() -> None:
    assert cookie_parts("xSESSDATA=no; SESSDATA=yes; bili_jct=csrf") == ("yes", "csrf")
    assert cookie_parts("SESSDATA=yes") == ("yes", None)


@pytest.mark.asyncio
async def test_http_transport_validates_response_at_boundary_and_closes_client() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"code": 0, "message": "0", "data": {"items": [], "next_cursor": None}}
        )

    transport = HttpxBilibiliTransport(httpx.MockTransport(handler))
    body = await transport("GET", "/x/web-interface/popular", "limit=1", None, b"")
    assert json.loads(body)["code"] == 0
    assert seen[0].headers["referer"] == "https://www.bilibili.com"
    assert transport.open_client_count == 0


@pytest.mark.asyncio
async def test_http_status_is_safe_and_classified() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="SECRET RAW BODY", request=request)

    transport = HttpxBilibiliTransport(httpx.MockTransport(handler))
    with pytest.raises(ContentIntegrationError) as raised:
        await transport("GET", "/x/test", "", None, b"")
    assert raised.value.code is IntegrationErrorCode.RATE_LIMITED
    assert "SECRET RAW BODY" not in str(raised.value)
