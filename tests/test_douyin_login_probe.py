"""Tests for the live Douyin login probe.

These lock in the conclusions of the 2026-07-18 strip-down control experiment
recorded in ``docs/plans/2026-07-18-source-auth-contract-spec.md`` D11. The
point of the regression net: if someone later sees Douyin reporting
``unverified`` again and concludes "Douyin can't be probed", these tests show
the discrimination is real and which codes carry it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openbiliclaw.sources.douyin_login_probe import (
    LOGIN_PROBE_PATH,
    DouyinAuthStatus,
    probe_douyin_login,
)

COOKIE = "sessionid=abc; sessionid_ss=abc; sid_tt=abc; ttwid=xyz"


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


def _json_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, content=json.dumps(payload), headers={"Content-Type": "application/json"}
    )


async def test_empty_cookie_reports_no_credential() -> None:
    status = await probe_douyin_login("   ")
    assert status == DouyinAuthStatus(
        has_cookie=False, authenticated=False, message="尚未同步抖音 Cookie。"
    )


async def test_logged_in_session_is_authenticated() -> None:
    """Treatment group of the D11 experiment: status_code=0 plus a real uid."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert LOGIN_PROBE_PATH in request.url.path
        assert request.headers["Cookie"] == COOKIE
        return _json_response({"status_code": 0, "user": {"uid": "110222331446", "nickname": "白"}})

    async with _client(handler) as http:
        status = await probe_douyin_login(COOKIE, http_client=http)

    assert status.authenticated is True
    assert status.network_error is False
    assert status.user_id == "110222331446"
    assert status.username == "白"
    assert "已登录抖音（白）" in status.message


async def test_logged_out_session_is_reported_as_expired() -> None:
    """Control group of the D11 experiment: status_code=8 '用户未登录'.

    This is the discriminator ``api/app.py`` claimed did not exist.
    """

    def handler(_: httpx.Request) -> httpx.Response:
        return _json_response({"status_code": 8, "status_msg": "用户未登录"})

    async with _client(handler) as http:
        status = await probe_douyin_login(COOKIE, http_client=http)

    assert status.has_cookie is True
    assert status.authenticated is False
    # A logged-out verdict must be definitive, not a transport excuse.
    assert status.network_error is False
    assert "失效" in status.message


@pytest.mark.parametrize(
    ("label", "handler_result"),
    [
        ("transport_error", httpx.ConnectError("boom")),
        ("http_500", httpx.Response(500)),
        ("non_json_body", httpx.Response(200, content=b"<html>risk control</html>")),
    ],
)
async def test_transport_failures_never_look_logged_out(label: str, handler_result: Any) -> None:
    """A flaky proxy must not be mistaken for an expired cookie."""

    def handler(_: httpx.Request) -> httpx.Response:
        if isinstance(handler_result, Exception):
            raise handler_result
        return handler_result

    async with _client(handler) as http:
        status = await probe_douyin_login(COOKIE, http_client=http)

    assert status.authenticated is False, label
    assert status.network_error is True, label
    assert status.has_cookie is True, label


async def test_unknown_status_code_is_indeterminate_not_logged_out() -> None:
    """An upstream error-surface change must not silently expire a good source."""

    def handler(_: httpx.Request) -> httpx.Response:
        return _json_response({"status_code": 2154, "status_msg": "something new"})

    async with _client(handler) as http:
        status = await probe_douyin_login(COOKIE, http_client=http)

    assert status.authenticated is False
    assert status.network_error is True
    assert "2154" in status.message


async def test_success_without_uid_is_indeterminate() -> None:
    """status_code=0 alone is not proof: the account payload must be present."""

    def handler(_: httpx.Request) -> httpx.Response:
        return _json_response({"status_code": 0, "user": {}})

    async with _client(handler) as http:
        status = await probe_douyin_login(COOKIE, http_client=http)

    assert status.authenticated is False
    assert status.network_error is True


async def test_probe_does_not_use_the_device_scoped_endpoint() -> None:
    """Guard against "simplifying" the probe to /aweme/v1/web/query/user/.

    That endpoint returns an identical 12-digit ``user_uid`` for logged-in and
    guest sessions (it is device-scoped), so probing it always reports success.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _json_response({"status_code": 0, "user": {"uid": "1", "nickname": "x"}})

    async with _client(handler) as http:
        await probe_douyin_login(COOKIE, http_client=http)

    assert seen == [LOGIN_PROBE_PATH]
    assert "/aweme/v1/web/query/user/" not in seen
