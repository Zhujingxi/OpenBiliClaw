from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from openbiliclaw.access.models import (
    AnonymousAccessHandle,
    CredentialAccessHandle,
    Permission,
    VerificationFailure,
)
from openbiliclaw.content.integration.actions import ActionConfirmation
from openbiliclaw.content.integration.capabilities import CreatorQuery, PageRequest
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.providers.bilibili.auth import BilibiliCredentialVerifier
from openbiliclaw.content.providers.bilibili.capabilities import (
    BilibiliActionRequest,
    BilibiliProvider,
)
from openbiliclaw.content.providers.bilibili.client import BilibiliClient, HttpxBilibiliTransport
from openbiliclaw.content.providers.bilibili.models import BilibiliNavData
from openbiliclaw.content.providers.bilibili.presentation import BILIBILI_PRESENTATION

FIXTURES = Path(__file__).with_name("fixtures")


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Routes:
    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes

    async def __call__(
        self, method: str, path: str, query: str, cookie: str | None, body: bytes
    ) -> bytes:
        return self.routes[path]


async def _resolve(handle: CredentialAccessHandle) -> str:
    return "SESSDATA=value; bili_jct=csrf"


def _provider(routes: dict[str, bytes]) -> BilibiliProvider:
    return BilibiliProvider(BilibiliClient(Routes(routes), _resolve))


def _anonymous(provider: str = "bilibili") -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id=provider,
        account_id=None,
        permissions=frozenset({Permission.READ_PUBLIC}),
    )


def _credential(*permissions: Permission) -> CredentialAccessHandle:
    return CredentialAccessHandle(
        provider_id="bilibili",
        account_id="42",
        permissions=frozenset(permissions),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )


def _ref(kind: str = "video", provider: str = "bilibili") -> ContentRef:
    content_id = "BV1TEST12345" if kind == "video" else "cv123"
    path = f"video/{content_id}" if kind == "video" else f"read/{content_id}"
    return ContentRef(
        provider_id=ProviderId(value=provider),
        content_kind=ContentKind(value=kind),
        provider_content_id=content_id,
        canonical_url=f"https://www.bilibili.com/{path}",
    )


@pytest.mark.asyncio
async def test_creator_and_empty_related_paths() -> None:
    provider = _provider(
        {
            "/x/space/wbi/arc/search": _fixture("empty.json"),
            "/x/web-interface/archive/related": _fixture("empty.json"),
        }
    )
    assert (await provider.creator(CreatorQuery(creator_id="42"), _anonymous())).items == ()
    assert (await provider.related(_ref(), PageRequest(), _anonymous())).items == ()


@pytest.mark.asyncio
async def test_public_access_and_related_kind_are_checked() -> None:
    provider = _provider({})
    with pytest.raises(ContentIntegrationError):
        await provider.fetch(_ref(), _anonymous("youtube"))
    with pytest.raises(ContentIntegrationError):
        await provider.related(_ref("article"), PageRequest(), _anonymous())


@pytest.mark.asyncio
async def test_unknown_and_expired_action_and_idempotency_scope_mismatch() -> None:
    provider = _provider({"/x/v3/fav/resource/deal": _fixture("action_success.json")})
    confirmation = ActionConfirmation(
        summary="confirmed", expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    unknown = BilibiliActionRequest(
        action_id="unknown",
        ref=_ref(),
        idempotency_key="workflow:unknown",
        confirmation=confirmation,
    )
    with pytest.raises(ContentIntegrationError):
        await provider.execute_action(unknown, _credential(Permission.WRITE))
    expired = unknown.model_copy(
        update={
            "action_id": "save",
            "confirmation": ActionConfirmation(
                summary="expired", expires_at=datetime.now(UTC) - timedelta(seconds=1)
            ),
        }
    )
    with pytest.raises(ContentIntegrationError):
        await provider.execute_action(expired, _credential(Permission.WRITE))
    first = BilibiliActionRequest(
        action_id="save",
        ref=_ref(),
        idempotency_key="workflow:shared",
        confirmation=confirmation,
    )
    await provider.execute_action(first, _credential(Permission.WRITE))
    other_ref = _ref().model_copy(
        update={
            "provider_content_id": "BV1OTHER1234",
            "canonical_url": "https://www.bilibili.com/video/BV1OTHER1234",
        }
    )
    other = first.model_copy(update={"ref": other_ref})
    with pytest.raises(ContentIntegrationError, match="idempotency key"):
        await provider.execute_action(other, _credential(Permission.WRITE))


@pytest.mark.asyncio
async def test_action_rejection_and_missing_csrf_are_safe() -> None:
    rejected = json.dumps({"code": 0, "message": "0", "data": {"accepted": False}}).encode()
    provider = _provider({"/x/v3/fav/resource/deal": rejected})
    request = BilibiliActionRequest(
        action_id="save",
        ref=_ref(),
        idempotency_key="workflow:reject",
        confirmation=ActionConfirmation(
            summary="confirmed", expires_at=datetime.now(UTC) + timedelta(minutes=1)
        ),
    )
    with pytest.raises(ContentIntegrationError):
        await provider.execute_action(request, _credential(Permission.WRITE))

    async def missing_csrf(handle: CredentialAccessHandle) -> str:
        return "SESSDATA=value"

    client = BilibiliClient(Routes({}), missing_csrf)
    with pytest.raises(ContentIntegrationError) as denied:
        await client.action("/x", {}, _credential(Permission.WRITE), idempotency_key="workflow:1")
    assert denied.value.code is IntegrationErrorCode.ACCESS_DENIED


@pytest.mark.asyncio
async def test_client_rejects_wrong_shapes_and_other_provider_code() -> None:
    wrong = json.dumps({"code": 0, "message": "0", "data": {"accepted": True}}).encode()
    client = BilibiliClient(Routes({"/page": wrong, "/item": wrong}), _resolve)
    with pytest.raises(ContentIntegrationError):
        await client.page("/page", {})
    with pytest.raises(ContentIntegrationError):
        await client.item("/item", {})
    other = json.dumps({"code": -500, "message": "SECRET", "data": None}).encode()
    with pytest.raises(ContentIntegrationError) as raised:
        await BilibiliClient(Routes({"/page": other}), _resolve).page("/page", {})
    assert "SECRET" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "failure"),
    [
        (b"not-json", VerificationFailure.INVALID_CREDENTIAL),
        (
            json.dumps({"cookie": "SESSDATA=value; bili_jct=csrf"}).encode(),
            VerificationFailure.RATE_LIMITED,
        ),
    ],
)
async def test_verifier_malformed_and_rate_limit(
    payload: bytes, failure: VerificationFailure
) -> None:
    routes = {"/x/web-interface/nav": _fixture("rate_limit.json")}
    verifier = BilibiliCredentialVerifier(BilibiliClient(Routes(routes), _resolve))
    result = await verifier(_credential(Permission.READ_PRIVATE), memoryview(payload))
    assert result.sanitized_failure is failure


@pytest.mark.asyncio
async def test_verifier_network_and_not_logged_in() -> None:
    class FailedTransport:
        async def __call__(
            self,
            method: str,
            path: str,
            query: str,
            cookie: str | None,
            body: bytes,
        ) -> bytes:
            raise ContentIntegrationError(IntegrationErrorCode.PROVIDER_UNAVAILABLE, "safe failure")

    verifier = BilibiliCredentialVerifier(BilibiliClient(FailedTransport(), _resolve))
    payload = memoryview(json.dumps({"cookie": "SESSDATA=value; bili_jct=csrf"}).encode())
    result = await verifier(_credential(Permission.READ_PRIVATE), payload)
    assert result.sanitized_failure is VerificationFailure.NETWORK_UNAVAILABLE

    nav = json.dumps(
        {"code": 0, "message": "0", "data": {"is_login": False, "mid": "0", "name": "guest"}}
    ).encode()
    verifier = BilibiliCredentialVerifier(
        BilibiliClient(Routes({"/x/web-interface/nav": nav}), _resolve)
    )
    result = await verifier(_credential(Permission.READ_PRIVATE), payload)
    assert result.sanitized_failure is VerificationFailure.EXPIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 429])
async def test_http_transport_statuses_and_transport_failure(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="SECRET", request=request)

    transport = HttpxBilibiliTransport(httpx.MockTransport(handler))
    with pytest.raises(ContentIntegrationError):
        await transport("GET", "/x", "", None, b"")

    async def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret host", request=request)

    with pytest.raises(ContentIntegrationError):
        await HttpxBilibiliTransport(httpx.MockTransport(broken))("GET", "/x", "", None, b"")


def test_projection_rejects_foreign_payload_and_presentation_descriptor() -> None:
    provider = _provider({})
    foreign = NativeContent(
        ref=_ref(),
        schema_version=1,
        payload=BilibiliNavData(is_login=True, mid="1", name="x"),
    )
    with pytest.raises(ValueError):
        provider.preview(foreign)
    with pytest.raises(ValueError):
        provider.observations(foreign)
    assert BILIBILI_PRESENTATION.accent_color == "#00AEEC"
