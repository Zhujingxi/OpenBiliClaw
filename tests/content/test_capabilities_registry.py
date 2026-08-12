from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.content.integration.actions import (
    ActionConfirmation,
    ActionRequest,
    ActionResult,
)
from openbiliclaw.content.integration.capabilities import (
    ContentPage,
    FetchCapability,
    PageRequest,
    ProviderCursor,
    SearchCapability,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import (
    ContentIntegrationError,
    IntegrationErrorCode,
)
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.content.integration.registry import ContentProviderRegistry
from openbiliclaw.content.integration.testing import validate_provider_contract

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle
    from openbiliclaw.content.integration.native import NativeContent
    from openbiliclaw.content.integration.projections import ContentPreview


class SearchOnly:
    async def search(self, query: SearchQuery, access: AccessHandle) -> ContentPage[ContentPreview]:
        return ContentPage(items=(), next_cursor=None)


class WrongAdvertised:
    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent:
        raise AssertionError


class SearchAndFetch(SearchOnly):
    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent:
        raise AssertionError


def _manifest(*capabilities: CapabilityKind) -> ProviderManifest:
    return ProviderManifest(
        provider_id=ProviderId(value="demo"),
        display_name="Demo",
        capabilities=frozenset(capabilities),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
        ),
        availability=ProviderAvailability.AVAILABLE,
    )


def test_cursor_is_opaque_provider_scoped_and_page_is_bounded() -> None:
    cursor = ProviderCursor(provider_id=ProviderId(value="demo"), value="opaque:do-not-parse")
    page = PageRequest(limit=20, cursor=cursor)
    assert page.cursor == cursor
    assert page.limit == 20
    with pytest.raises(ValueError):
        PageRequest(limit=0)


def test_manifest_is_frozen_and_rejects_duplicate_native_schema_identity() -> None:
    manifest = _manifest(CapabilityKind.SEARCH)
    assert manifest.model_config["frozen"] is True
    with pytest.raises(ValueError, match="duplicate native schema"):
        ProviderManifest(
            provider_id=ProviderId(value="demo"),
            display_name="Demo",
            capabilities=frozenset(),
            native_schemas=(
                NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
                NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
            ),
            availability=ProviderAvailability.AVAILABLE,
        )


def test_registry_rejects_missing_advertised_capability_and_duplicates() -> None:
    registry = ContentProviderRegistry()
    with pytest.raises(ContentIntegrationError) as raised:
        registry.register(_manifest(CapabilityKind.SEARCH), WrongAdvertised())
    assert raised.value.code is IntegrationErrorCode.UNAVAILABLE_CAPABILITY
    registry.register(_manifest(CapabilityKind.SEARCH), SearchOnly())
    with pytest.raises(ContentIntegrationError) as duplicate:
        registry.register(_manifest(CapabilityKind.SEARCH), SearchOnly())
    assert duplicate.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE
    assert registry.manifests() == (_manifest(CapabilityKind.SEARCH),)


def test_registry_returns_narrow_runtime_checked_capabilities() -> None:
    registry = ContentProviderRegistry()
    registry.register(_manifest(CapabilityKind.SEARCH, CapabilityKind.FETCH), SearchAndFetch())
    implementation = registry.provider(ProviderId(value="demo"))
    assert isinstance(implementation, SearchCapability)
    assert isinstance(implementation, FetchCapability)
    with pytest.raises(ContentIntegrationError):
        registry.provider(ProviderId(value="missing"))


def test_reusable_provider_contract_validator() -> None:
    assert validate_provider_contract(_manifest(CapabilityKind.SEARCH), SearchOnly()) == ()
    violations = validate_provider_contract(_manifest(CapabilityKind.SEARCH), WrongAdvertised())
    assert violations == ("advertised search capability is not implemented",)


def test_actions_require_idempotency_and_confirmation_metadata() -> None:
    ref = ContentRef(
        provider_id=ProviderId(value="demo"),
        content_kind=ContentKind(value="post"),
        provider_content_id="1",
        canonical_url="https://example.com/1",
    )
    request = ActionRequest(
        action_id="save",
        ref=ref,
        idempotency_key="workflow:123",
        confirmation=ActionConfirmation(summary="Save this post?", expires_at=datetime.now(UTC)),
    )
    result = ActionResult(
        action_id="save",
        ref=ref,
        idempotency_key=request.idempotency_key,
        completed_at=datetime.now(UTC),
    )
    assert result.idempotency_key == request.idempotency_key
    with pytest.raises(ValueError):
        ActionRequest.model_validate({"action_id": "save", "ref": ref})


def test_safe_normalized_error_contains_no_cause_or_payload() -> None:
    error = ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "access denied")
    assert str(error) == "access_denied: access denied"
    assert not hasattr(error, "payload")
