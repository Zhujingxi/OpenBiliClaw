from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openbiliclaw.access.models import (
    AnonymousAccessHandle,
    CredentialAccessHandle,
    Permission,
)
from openbiliclaw.content.integration.actions import ActionConfirmation
from openbiliclaw.content.integration.capabilities import (
    FeedQuery,
    PageRequest,
    ProviderCursor,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.testing import validate_provider_contract
from openbiliclaw.content.providers.bilibili.auth import BILIBILI_CONNECTION_FORM
from openbiliclaw.content.providers.bilibili.capabilities import (
    BilibiliActionRequest,
    BilibiliProvider,
)
from openbiliclaw.content.providers.bilibili.client import BilibiliClient
from openbiliclaw.content.providers.bilibili.manifest import BILIBILI_MANIFEST
from openbiliclaw.content.providers.bilibili.models import BilibiliResponse

if TYPE_CHECKING:
    from openbiliclaw.content.providers.bilibili.client import CredentialResolver

FIXTURES = Path(__file__).with_name("fixtures")


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _anonymous() -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id="bilibili", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )


def _credential(*permissions: Permission) -> CredentialAccessHandle:
    return CredentialAccessHandle(
        provider_id="bilibili",
        account_id="42",
        permissions=frozenset(permissions),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )


class FixtureTransport:
    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, str | None, bytes]] = []

    async def __call__(
        self,
        method: str,
        path: str,
        query: str,
        cookie: str | None,
        body: bytes,
    ) -> bytes:
        self.requests.append((method, f"{path}?{query}", cookie, body))
        return self.routes[path]


class Resolver:
    def __init__(self, value: str | None = None, seen: list[str] | None = None) -> None:
        self.value = value
        self.seen = seen

    async def __call__(self, handle: CredentialAccessHandle) -> str:
        if self.seen is not None:
            self.seen.append(handle.credential_ref)
        if self.value is None:
            raise AssertionError("credential must not be resolved")
        return self.value


def _client(
    transport: FixtureTransport,
    resolver: CredentialResolver | None = None,
) -> BilibiliClient:
    return BilibiliClient(transport, resolver or Resolver())


def test_manifest_matches_complete_reference_provider_contract() -> None:
    client = _client(FixtureTransport({}))
    provider = BilibiliProvider(client)
    assert validate_provider_contract(BILIBILI_MANIFEST, provider) == ()
    assert {schema.content_kind.value for schema in BILIBILI_MANIFEST.native_schemas} == {
        "video",
        "article",
    }


@pytest.mark.parametrize("fixture", ["search_success.json", "empty.json", "tombstone.json"])
def test_external_fixtures_validate(fixture: str) -> None:
    BilibiliResponse.model_validate_json(_fixture(fixture))


def test_schema_drift_and_malformed_payload_fail_closed() -> None:
    with pytest.raises(ValidationError):
        BilibiliResponse.model_validate_json(_fixture("schema_drift.json"))
    with pytest.raises(ValidationError):
        BilibiliResponse.model_validate({"code": 0, "data": {"kind": "video"}})


@pytest.mark.asyncio
async def test_search_is_bounded_and_cursor_is_opaque() -> None:
    transport = FixtureTransport({"/x/web-interface/search/type": _fixture("search_success.json")})
    provider = BilibiliProvider(_client(transport))
    page = await provider.search(
        SearchQuery(
            text="typed",
            page=PageRequest(
                limit=1,
                cursor=ProviderCursor(provider_id=ProviderId(value="bilibili"), value="page:7"),
            ),
        ),
        _anonymous(),
    )
    assert len(page.items) == 1
    assert page.items[0].ref.provider_content_id == "BV1TEST12345"
    assert page.next_cursor == ProviderCursor(
        provider_id=ProviderId(value="bilibili"), value="page:2"
    )
    assert "cursor=page%3A7" in transport.requests[0][1]


@pytest.mark.asyncio
async def test_empty_feed_and_explicit_browser_session_degradation() -> None:
    provider = BilibiliProvider(
        _client(FixtureTransport({"/x/web-interface/popular": _fixture("empty.json")}))
    )
    page = await provider.feed(FeedQuery(), _anonymous())
    assert page.items == ()
    with pytest.raises(ContentIntegrationError) as exc:
        await provider.feed(FeedQuery(feed_id="rendered_homepage"), _anonymous())
    assert exc.value.code is IntegrationErrorCode.UNAVAILABLE_CAPABILITY
    assert "browser" not in exc.value.safe_message.lower()


@pytest.mark.asyncio
async def test_fetch_video_article_tombstone_and_related() -> None:
    transport = FixtureTransport(
        {
            "/x/web-interface/view": _fixture("tombstone.json"),
            "/x/article/viewinfo": _fixture("article.json"),
            "/x/web-interface/archive/related": _fixture("related.json"),
        }
    )
    provider = BilibiliProvider(_client(transport))
    video_ref = ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="video"),
        provider_content_id="BV1DEAD12345",
        canonical_url="https://www.bilibili.com/video/BV1DEAD12345",
    )
    native = await provider.fetch(video_ref, _anonymous())
    assert '"availability":"tombstone"' in native.payload.model_dump_json()
    article_ref = ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="article"),
        provider_content_id="cv123",
        canonical_url="https://www.bilibili.com/read/cv123",
    )
    article = await provider.fetch(article_ref, _anonymous())
    assert '"title":"Typed article"' in article.payload.model_dump_json()
    related = await provider.related(video_ref, PageRequest(limit=1), _anonymous())
    assert related.items[0].title == "Related"


@pytest.mark.asyncio
async def test_history_saved_require_private_scope_and_resolve_cookie_only_in_client() -> None:
    canary = "SESSDATA=CANARY; bili_jct=csrf"
    seen: list[str] = []

    transport = FixtureTransport(
        {
            "/x/web-interface/history/cursor": _fixture("empty.json"),
            "/x/v3/fav/resource/list": _fixture("empty.json"),
        }
    )
    provider = BilibiliProvider(_client(transport, Resolver(canary, seen)))
    with pytest.raises(ContentIntegrationError) as denied:
        await provider.history(PageRequest(), _anonymous())
    assert denied.value.code is IntegrationErrorCode.ACCESS_DENIED
    await provider.history(PageRequest(), _credential(Permission.READ_PRIVATE))
    await provider.saved(PageRequest(), _credential(Permission.READ_PRIVATE))
    assert seen == ["cred_" + "a" * 32, "cred_" + "a" * 32]
    assert canary not in repr(provider)
    assert all(request[2] == canary for request in transport.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("auth_failure.json", IntegrationErrorCode.ACCESS_DENIED),
        ("rate_limit.json", IntegrationErrorCode.RATE_LIMITED),
    ],
)
async def test_provider_failures_are_normalized_without_body_or_cookie(
    fixture: str, code: IntegrationErrorCode
) -> None:
    transport = FixtureTransport({"/x/web-interface/search/type": _fixture(fixture)})
    provider = BilibiliProvider(_client(transport))
    with pytest.raises(ContentIntegrationError) as raised:
        await provider.search(SearchQuery(text="x"), _anonymous())
    assert raised.value.code is code
    assert "request was banned" not in str(raised.value)
    assert "账号未登录" not in str(raised.value)


def test_manual_cookie_form_is_secret_and_shape_validated() -> None:
    field = BILIBILI_CONNECTION_FORM.fields[0]
    assert field.field_id == "cookie" and field.secret is True
    submission = BILIBILI_CONNECTION_FORM.validate_submission(
        {"cookie": "SESSDATA=value; bili_jct=csrf"}
    )
    assert "SESSDATA=value" not in repr(submission)
    with pytest.raises(ValueError):
        BILIBILI_CONNECTION_FORM.validate_submission({"cookie": "SESSDATA=only"})


@pytest.mark.asyncio
async def test_action_requires_write_scope_csrf_and_is_idempotent() -> None:
    canary = "SESSDATA=CANARY; bili_jct=csrf123"

    transport = FixtureTransport({"/x/v3/fav/resource/deal": _fixture("action_success.json")})
    provider = BilibiliProvider(_client(transport, Resolver(canary)))
    ref = ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="video"),
        provider_content_id="BV1TEST12345",
        canonical_url="https://www.bilibili.com/video/BV1TEST12345",
    )
    request = BilibiliActionRequest(
        action_id="save",
        ref=ref,
        idempotency_key="workflow:123",
        confirmation=ActionConfirmation(
            summary="Save this video", expires_at=datetime.now(UTC) + timedelta(minutes=1)
        ),
    )
    with pytest.raises(ContentIntegrationError):
        await provider.execute_action(request, _credential(Permission.READ_PRIVATE))
    result = await provider.execute_action(request, _credential(Permission.WRITE))
    repeated = await provider.execute_action(request, _credential(Permission.WRITE))
    assert result == repeated
    assert len(transport.requests) == 1
    body = transport.requests[0][3].decode()
    assert "csrf=csrf123" in body
    assert "CANARY" not in body


@pytest.mark.asyncio
async def test_wrong_provider_cursor_and_reference_fail_before_transport() -> None:
    provider = BilibiliProvider(_client(FixtureTransport({})))
    with pytest.raises(ContentIntegrationError) as cursor_error:
        await provider.search(
            SearchQuery(
                text="x",
                page=PageRequest(
                    cursor=ProviderCursor(provider_id=ProviderId(value="youtube"), value="1")
                ),
            ),
            _anonymous(),
        )
    assert cursor_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF
    wrong = ContentRef(
        provider_id=ProviderId(value="youtube"),
        content_kind=ContentKind(value="video"),
        provider_content_id="x",
        canonical_url="https://youtube.com/watch?v=x",
    )
    with pytest.raises(ContentIntegrationError):
        await provider.fetch(wrong, _anonymous())


def test_projections_are_separate_and_card_snapshot_is_stable() -> None:
    payload = json.loads(_fixture("search_success.json"))["data"]["items"][0]
    provider = BilibiliProvider(_client(FixtureTransport({})))
    native = provider.native_from_payload(payload)
    preview = provider.preview(native)
    candidate = provider.recommendation_candidate(native)
    document = provider.search_document(native)
    card = provider.card_data(native)
    assert preview.provenance.ref == preview.ref
    assert candidate.discovery_reason == "bilibili:public_feed"
    assert '"badge"' not in document.model_dump_json()
    assert '"discovery_reason"' not in card.model_dump_json()
    assert json.loads(card.model_dump_json()) == {
        "ref": {
            "provider_id": {"value": "bilibili"},
            "content_kind": {"value": "video"},
            "provider_content_id": "BV1TEST12345",
            "canonical_url": "https://www.bilibili.com/video/BV1TEST12345",
        },
        "title": "Typed refactor",
        "summary": "A useful video",
        "badge": "Bilibili · 1000 views",
        "image_url": "https://i.example/video.jpg",
        "source_timestamp": "2023-11-14T22:13:20Z",
        "provenance": {
            "ref": {
                "provider_id": {"value": "bilibili"},
                "content_kind": {"value": "video"},
                "provider_content_id": "BV1TEST12345",
                "canonical_url": "https://www.bilibili.com/video/BV1TEST12345",
            },
            "native_schema_version": 1,
            "projected_at": "2023-11-14T22:13:20Z",
        },
    }


def test_observations_are_safe_and_source_timestamped() -> None:
    payload = json.loads(_fixture("search_success.json"))["data"]["items"][0]
    provider = BilibiliProvider(_client(FixtureTransport({})))
    observations = provider.observations(provider.native_from_payload(payload))
    assert observations[0].event_type == "content_seen"
    assert observations[0].occurred_at.tzinfo is not None
