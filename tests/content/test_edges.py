from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from openbiliclaw.access.models import AnonymousAccessHandle, Permission
from openbiliclaw.content.integration.capabilities import (
    ContentFilter,
    FetchCapability,
    ProjectionCapability,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.manifest import (
    ActionDescriptor,
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)
from openbiliclaw.content.integration.registry import ContentProviderRegistry
from openbiliclaw.content.integration.tools import ToolBudget, build_provider_tools
from openbiliclaw.core._pydantic import StrictBaseModel

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle


class Payload(StrictBaseModel):
    title: str


def _ref() -> ContentRef:
    return ContentRef(
        provider_id=ProviderId(value="demo"),
        content_kind=ContentKind(value="post"),
        provider_content_id="1",
        canonical_url="https://example.com/1",
    )


def _provenance(ref: ContentRef | None = None) -> ProjectionProvenance:
    return ProjectionProvenance(
        ref=ref or _ref(), native_schema_version=1, projected_at=datetime.now(UTC)
    )


def _manifest(*capabilities: CapabilityKind) -> ProviderManifest:
    return ProviderManifest(
        provider_id=ProviderId(value="demo"),
        display_name="Demo",
        capabilities=frozenset(capabilities),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="post"), schema_version=1),
        ),
        availability=ProviderAvailability.AVAILABLE,
    )


def _access() -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id="demo", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )


def test_value_object_string_and_filter_time_order() -> None:
    assert str(ProviderId(value="demo")) == "demo"
    assert str(ContentKind(value="post")) == "post"
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="published_after"):
        ContentFilter(published_after=now, published_before=now - timedelta(seconds=1))


def test_manifest_rejects_duplicate_actions_and_action_mismatch() -> None:
    action = ActionDescriptor(
        action_id="save", label="Save", content_kind=ContentKind(value="post")
    )
    with pytest.raises(ValidationError, match="duplicate action"):
        ProviderManifest(
            provider_id=ProviderId(value="demo"),
            display_name="Demo",
            capabilities=frozenset({CapabilityKind.ACTION}),
            native_schemas=(),
            actions=(action, action),
            availability=ProviderAvailability.AVAILABLE,
        )
    with pytest.raises(ValidationError, match="must agree"):
        ProviderManifest(
            provider_id=ProviderId(value="demo"),
            display_name="Demo",
            capabilities=frozenset(),
            native_schemas=(),
            actions=(action,),
            availability=ProviderAvailability.AVAILABLE,
        )


def test_projection_rejects_mismatched_provenance() -> None:
    wrong_ref = ContentRef(
        provider_id=ProviderId(value="demo"),
        content_kind=ContentKind(value="post"),
        provider_content_id="2",
        canonical_url="https://example.com/2",
    )
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="provenance"):
        ContentPreview(
            ref=_ref(),
            title="x",
            summary="",
            source_timestamp=now,
            provenance=_provenance(wrong_ref),
        )
    with pytest.raises(ValidationError, match="provenance"):
        RecommendationCandidate(
            ref=_ref(),
            title="x",
            summary="",
            discovery_reason="feed",
            source_timestamp=now,
            provenance=_provenance(wrong_ref),
        )
    with pytest.raises(ValidationError, match="provenance"):
        SearchDocument(
            ref=_ref(),
            title="x",
            body="body",
            source_timestamp=now,
            provenance=_provenance(wrong_ref),
        )
    with pytest.raises(ValidationError, match="provenance"):
        CardData(
            ref=_ref(),
            title="x",
            summary="",
            source_timestamp=now,
            provenance=_provenance(wrong_ref),
        )


class FetchAndProject:
    def __init__(self) -> None:
        self.fetched: list[ContentRef] = []

    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent:
        self.fetched.append(ref)
        return NativeContent(ref=ref, schema_version=1, payload=Payload(title="long title"))

    def preview(self, content: NativeContent) -> ContentPreview:
        now = datetime.now(UTC)
        return ContentPreview(
            ref=content.ref,
            title="long title",
            summary="long summary",
            creator_label=None,
            source_timestamp=now,
            provenance=ProjectionProvenance(
                ref=content.ref, native_schema_version=content.schema_version, projected_at=now
            ),
        )

    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate:
        raise AssertionError

    def search_document(self, content: NativeContent) -> SearchDocument:
        raise AssertionError

    def card_data(self, content: NativeContent) -> CardData:
        raise AssertionError


def test_projection_protocols_are_runtime_checkable() -> None:
    provider = FetchAndProject()
    assert isinstance(provider, FetchCapability)
    assert isinstance(provider, ProjectionCapability)


async def test_generated_fetch_tool_executes_typed_capability_and_bounds_result() -> None:
    provider = FetchAndProject()
    manifest = _manifest(CapabilityKind.FETCH, CapabilityKind.PROJECTION)
    tools = build_provider_tools(
        manifest,
        provider,
        _access(),
        enabled=frozenset({CapabilityKind.FETCH}),
        budget=ToolBudget(max_title_chars=4, max_summary_chars=4),
    )
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "demo_fetch",
                        {
                            "content_kind": "post",
                            "provider_content_id": "1",
                            "canonical_url": "https://example.com/1",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[None, str] = Agent(FunctionModel(respond), tools=tools)
    result = await agent.run("fetch")
    assert result.output == "done"
    assert provider.fetched == [_ref()]
    tool_return = result.all_messages_json().decode()
    assert "long title" not in tool_return
    assert "long summary" not in tool_return
    assert '"title":"long"' in tool_return


def test_tool_builder_rejects_cross_provider_access_handle() -> None:
    wrong_access = AnonymousAccessHandle(
        provider_id="other",
        account_id=None,
        permissions=frozenset({Permission.READ_PUBLIC}),
    )
    with pytest.raises(ContentIntegrationError, match="another provider"):
        build_provider_tools(
            _manifest(CapabilityKind.SEARCH),
            object(),
            wrong_access,
            enabled=frozenset({CapabilityKind.SEARCH}),
            budget=ToolBudget(),
        )


def test_registry_manifest_and_sorting_and_missing_tool_implementations() -> None:
    registry = ContentProviderRegistry()
    provider = FetchAndProject()
    manifest = _manifest(CapabilityKind.FETCH, CapabilityKind.PROJECTION)
    registry.register(manifest, provider)
    assert registry.manifest(ProviderId(value="demo")) == manifest
    with pytest.raises(ContentIntegrationError):
        registry.manifest(ProviderId(value="missing"))
    with pytest.raises(ContentIntegrationError, match="search capability"):
        build_provider_tools(
            _manifest(CapabilityKind.SEARCH),
            object(),
            _access(),
            enabled=frozenset({CapabilityKind.SEARCH}),
            budget=ToolBudget(),
        )
    with pytest.raises(ContentIntegrationError, match="fetch projection"):
        build_provider_tools(
            _manifest(CapabilityKind.FETCH),
            object(),
            _access(),
            enabled=frozenset({CapabilityKind.FETCH}),
            budget=ToolBudget(),
        )
