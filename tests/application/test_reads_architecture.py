from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.access.models import (
    AccessStatus,
    AccessStatusKind,
    AnonymousAccessHandle,
    Permission,
)
from openbiliclaw.application.errors import ApplicationError
from openbiliclaw.application.reads import (
    GetContentDetails,
    GetContentDetailsQuery,
    GetJobHealth,
    GetRecommendations,
    GetRecommendationsQuery,
    GetSourceStatus,
    GetSourceStatusQuery,
    ListSources,
    ListSourcesQuery,
    SearchContent,
    SearchContentQuery,
    ShowProfile,
    ShowProfileQuery,
)
from openbiliclaw.content.integration.capabilities import ContentPage
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import ContentPreview
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.health import HealthSnapshot, HealthStatus
from openbiliclaw.understanding.profile import CanonicalProfile

if TYPE_CHECKING:
    from openbiliclaw.recommendation.models import SelectionRecord

NOW = datetime(2030, 1, 1, tzinfo=UTC)
REF = ContentRef(
    provider_id=ProviderId(value="demo"),
    content_kind=ContentKind(value="video"),
    provider_content_id="1",
    canonical_url="https://demo.example/1",
)
HANDLE = AnonymousAccessHandle(
    provider_id="demo", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
)


class Payload(StrictBaseModel):
    title: str


class AccessFake:
    async def status(self, provider_id: str, account_id: str | None) -> AccessStatus:
        return AccessStatus(
            provider_id=provider_id,
            account_id=account_id,
            state=AccessStatusKind.CONNECTED,
            method_id="builtin.anonymous",
        )

    def connected_handle(self, provider_id: str, account_id: str | None) -> AnonymousAccessHandle:
        return HANDLE


class RecommendationsFake:
    async def feed(self, *, limit: int) -> tuple[SelectionRecord, ...]:
        return ()


class SearchProvider:
    async def search(self, query: object, access: object) -> ContentPage[ContentPreview]:
        return ContentPage[ContentPreview](items=(), next_cursor=None)

    async def fetch(self, ref: ContentRef, access: object) -> NativeContent:
        return NativeContent(ref=ref, schema_version=1, payload=Payload(title="ok"))


class RegistryFake:
    def provider(self, provider_id: ProviderId) -> SearchProvider:
        return SearchProvider()


class UnderstandingFake:
    async def profile(self, profile_id: str) -> CanonicalProfile:
        return CanonicalProfile.empty(profile_id, NOW)


class HealthFake:
    def health(self) -> HealthSnapshot:
        return HealthSnapshot(component_id="runtime", status=HealthStatus.HEALTHY, checked_at=NOW)


async def test_read_workflows_are_model_free_and_bounded() -> None:
    status = await GetSourceStatus(AccessFake())(GetSourceStatusQuery(provider_id="demo"))
    assert status.status.state is AccessStatusKind.CONNECTED
    listed = await ListSources(("demo",), AccessFake())(ListSourcesQuery())
    assert listed.items[0].provider_id == "demo"
    assert not (await GetRecommendations(RecommendationsFake())(GetRecommendationsQuery())).items
    assert not (
        await SearchContent(RegistryFake(), AccessFake())(
            SearchContentQuery(provider_id=ProviderId(value="demo"), text="science")
        )
    ).items
    detail = await GetContentDetails(RegistryFake(), AccessFake())(GetContentDetailsQuery(ref=REF))
    assert detail.content.ref == REF
    profile = await ShowProfile(UnderstandingFake())(ShowProfileQuery(profile_id="default"))
    assert profile.profile.version == 1
    assert (await GetJobHealth(HealthFake())()).health.status is HealthStatus.HEALTHY
    with pytest.raises(ValueError):
        GetRecommendationsQuery(limit=101)
    with pytest.raises(ApplicationError):
        await SearchContent(RegistryFake(), DisconnectedAccess())(
            SearchContentQuery(provider_id=ProviderId(value="demo"), text="science")
        )


class DisconnectedAccess:
    async def status(self, provider_id: str, account_id: str | None) -> AccessStatus:
        return AccessStatus(
            provider_id=provider_id,
            account_id=account_id,
            state=AccessStatusKind.DISCONNECTED,
        )

    def connected_handle(self, provider_id: str, account_id: str | None) -> None:
        return None


def _imports(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(item.name for item in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()


def test_application_imports_only_product_contracts() -> None:
    from pathlib import Path

    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "application"
    forbidden = (
        "openbiliclaw.hosts",
        "openbiliclaw.assistant",
        "openbiliclaw.api",
        "openbiliclaw.cli",
        "openbiliclaw.infrastructure",
    )
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            for module in _imports(node):
                if module.startswith(forbidden):
                    violations.append(f"{path.name}:{module}")
                root_name = module.split(".")[0]
                assert (
                    (isinstance(node, ast.ImportFrom) and node.level)
                    or root_name in sys.stdlib_module_names
                    or root_name
                    in {
                        "openbiliclaw",
                        "pydantic",
                    }
                )
    assert not violations
