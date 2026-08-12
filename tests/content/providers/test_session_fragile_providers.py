from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError
from openbiliclaw.content.integration.manifest import CapabilityKind, ProviderAvailability
from openbiliclaw.content.integration.testing import validate_provider_contract
from openbiliclaw.content.providers.douyin.capabilities import DouyinProvider
from openbiliclaw.content.providers.douyin.manifest import DOUYIN_MANIFEST
from openbiliclaw.content.providers.douyin.models import DouyinResponse
from openbiliclaw.content.providers.douyin.presentation import DOUYIN_PRESENTATION
from openbiliclaw.content.providers.rednote.capabilities import RednoteProvider
from openbiliclaw.content.providers.rednote.client import RednoteClient
from openbiliclaw.content.providers.rednote.manifest import REDNOTE_MANIFEST
from openbiliclaw.content.providers.rednote.models import RednoteResponse
from openbiliclaw.content.providers.rednote.presentation import REDNOTE_PRESENTATION

SUCCESS_DOUYIN = (
    b'{"status_code":0,"items":[{"aweme_id":"7390000000000000001",'
    b'"desc":"Typed Douyin","author":{"sec_uid":"MS4wLjAB","nickname":"Creator"},'
    b'"video":{"cover_url":"https://img.example/dy.jpg","duration_ms":12000},'
    b'"statistics":{"play_count":100,"digg_count":8,"comment_count":2,'
    b'"collect_count":3,"share_count":1},"create_time":1700000000,'
    b'"availability":"available"}],"next_cursor":"20"}'
)
SUCCESS_REDNOTE = (
    b'{"code":0,"items":[{"note_id":"65abc123def4567890abcd12",'
    b'"title":"Typed RedNote","description":"Useful note",'
    b'"author":{"user_id":"u1","nickname":"Creator"},'
    b'"cover_url":"https://img.example/xhs.jpg","published_at":1700000000,'
    b'"likes":9,"availability":"available"}],"next_cursor":"cursor-2"}'
)


def test_manifests_are_degraded_and_projection_only() -> None:
    rednote = RednoteProvider()
    douyin = DouyinProvider()
    assert validate_provider_contract(REDNOTE_MANIFEST, rednote) == ()
    assert validate_provider_contract(DOUYIN_MANIFEST, douyin) == ()
    assert REDNOTE_MANIFEST.availability is ProviderAvailability.DEGRADED
    assert DOUYIN_MANIFEST.availability is ProviderAvailability.DEGRADED
    assert (
        REDNOTE_MANIFEST.capabilities
        == DOUYIN_MANIFEST.capabilities
        == frozenset({CapabilityKind.PROJECTION})
    )
    assert REDNOTE_MANIFEST.actions == DOUYIN_MANIFEST.actions == ()


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (DouyinResponse, SUCCESS_DOUYIN),
        (RednoteResponse, SUCCESS_REDNOTE),
        (DouyinResponse, b'{"status_code":0,"items":[],"next_cursor":null}'),
        (RednoteResponse, b'{"code":0,"items":[],"next_cursor":null}'),
        (
            DouyinResponse,
            b'{"status_code":-101,"items":[],"next_cursor":null}',
        ),
        (
            RednoteResponse,
            b'{"code":-101,"items":[],"next_cursor":null}',
        ),
        (
            DouyinResponse,
            b'{"status_code":429,"items":[],"next_cursor":null}',
        ),
        (
            RednoteResponse,
            b'{"code":429,"items":[],"next_cursor":null}',
        ),
        (
            DouyinResponse,
            b'{"status_code":0,"items":[{"aweme_id":"7390000000000000002",'
            b'"desc":"gone","author":null,"video":null,"statistics":null,'
            b'"create_time":0,"availability":"tombstone"}],"next_cursor":null}',
        ),
        (
            RednoteResponse,
            b'{"code":0,"items":[{"note_id":"65abc123def4567890abcd13",'
            b'"title":"gone","description":"","author":null,"cover_url":null,'
            b'"published_at":0,"likes":0,"availability":"tombstone"}],'
            b'"next_cursor":null}',
        ),
    ],
)
def test_success_empty_auth_rate_limit_and_tombstone_fixtures_validate(
    model: type[BaseModel], payload: bytes
) -> None:
    model.model_validate_json(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            DouyinResponse,
            b'{"status_code":0,"items":[{"aweme_id":"1","unknown":1}],"next_cursor":null}',
        ),
        (
            RednoteResponse,
            b'{"code":0,"items":[{"note_id":"1","unknown":1}],"next_cursor":null}',
        ),
    ],
)
def test_schema_drift_fails_closed(model: type[BaseModel], payload: bytes) -> None:
    with pytest.raises(ValidationError):
        model.model_validate_json(payload)


def test_projections_and_presentation_are_stable() -> None:
    dy_provider = DouyinProvider()
    dy = dy_provider.native_from_model(DouyinResponse.model_validate_json(SUCCESS_DOUYIN).items[0])
    xhs_provider = RednoteProvider()
    xhs = xhs_provider.native_from_model(
        RednoteResponse.model_validate_json(SUCCESS_REDNOTE).items[0]
    )
    for provider, native in ((dy_provider, dy), (xhs_provider, xhs)):
        preview = provider.preview(native)
        card = provider.card_data(native)
        candidate = provider.recommendation_candidate(native)
        document = provider.search_document(native)
        assert preview.provenance.ref == preview.ref
        assert card.source_timestamp == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        assert "discovery_reason" not in card.model_dump_json()
        assert "badge" not in document.model_dump_json()
        assert candidate.discovery_reason.startswith(native.ref.provider_id.value)
    assert DOUYIN_PRESENTATION.supported_kinds == ("short_video",)
    assert REDNOTE_PRESENTATION.supported_kinds == ("note",)
    assert "CANARY" not in repr(dy_provider)
    assert "CANARY" not in repr(xhs_provider)


def test_projection_payload_type_is_enforced() -> None:
    dy_provider = DouyinProvider()
    xhs_provider = RednoteProvider()
    dy_native = dy_provider.native_from_model(
        DouyinResponse.model_validate_json(SUCCESS_DOUYIN).items[0]
    )
    xhs_native = xhs_provider.native_from_model(
        RednoteResponse.model_validate_json(SUCCESS_REDNOTE).items[0]
    )
    with pytest.raises(ValueError):
        dy_provider.preview(xhs_native)
    with pytest.raises(ValueError):
        xhs_provider.preview(dy_native)


def test_rednote_parse_boundary_normalizes_failures_without_body() -> None:
    client = RednoteClient()
    for body in (
        b'{"code":-101,"items":[],"next_cursor":null}',
        b'{"code":429,"items":[],"next_cursor":null}',
        b'{"code":500,"items":[],"next_cursor":null}',
        b"not-json",
    ):
        with pytest.raises(ContentIntegrationError) as raised:
            client.parse(body)
        assert body.decode() not in str(raised.value)
