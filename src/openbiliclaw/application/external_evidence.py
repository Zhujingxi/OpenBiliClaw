"""Bounded credentialed history/save ingestion through the observation ledger."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.access.models import AccessHandle, CredentialAccessHandle, Permission
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.content.integration.capabilities import (
    ContentPage,
    HistoryCapability,
    PageRequest,
    SavedCapability,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError
from openbiliclaw.content.integration.identity import ContentRef, ProviderId
from openbiliclaw.content.integration.manifest import CapabilityKind, ProviderManifest
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.jobs import (
    IntervalSchedule,
    JobSpec,
    MissedRunPolicy,
    OverlapPolicy,
)
from openbiliclaw.observations.models import (
    ExternalContentPayload,
    ExternalHistoryViewObservation,
    ExternalSaveObservation,
    Observation,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.service import RecordBatchResult, RecordStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime
    from pathlib import Path

    from openbiliclaw.content.integration.projections import ContentPreview

EXTERNAL_EVIDENCE_SYNC_INTERVAL_SECONDS = 900
_MAX_PAGES = 2
_PAGE_SIZE = 50
_MAX_IMPORT_ITEMS = 200
_ALLOWED_EVENTS = frozenset({"external_history_view", "external_save"})


class ProviderRegistry(Protocol):
    def manifests(self) -> tuple[ProviderManifest, ...]: ...
    def manifest(self, provider_id: ProviderId) -> ProviderManifest: ...
    def provider(self, provider_id: ProviderId) -> object: ...


class AccessReads(Protocol):
    def connected_handle(self, provider_id: str, account_id: str | None) -> AccessHandle | None: ...


class ExternalImportItem(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: str = Field(pattern=r"^external_(?:history_view|save)$")
    ref: ContentRef
    title: str = Field(min_length=1, max_length=500)
    creator_label: str | None = Field(default=None, max_length=300)
    occurred_at: AwareDatetime


class ExternalImportBatch(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ExternalImportItem, ...]
    ignored: int = Field(default=0, ge=0)
    warnings: tuple[str, ...] = ()


class ExternalEvidenceImporter(Protocol):
    def __call__(self, path: Path, fallback_time: datetime) -> ExternalImportBatch: ...


class ObservationIngress(Protocol):
    async def record_batch(
        self, observations: tuple[Observation, ...], *, allowed_event_types: frozenset[str]
    ) -> RecordBatchResult: ...


class ExternalEvidenceResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    inserted: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    ignored: int = Field(default=0, ge=0)
    skipped: bool = False
    warnings: tuple[str, ...] = ()


class ExternalEvidenceIngestion:
    """Normalize provider-owned evidence without bypassing observation ingress."""

    def __init__(
        self,
        registry: ProviderRegistry,
        access: AccessReads,
        ingress: ObservationIngress,
        *,
        clock: Callable[[], datetime],
        importers: Mapping[str, ExternalEvidenceImporter] | None = None,
    ) -> None:
        self._registry = registry
        self._access = access
        self._ingress = ingress
        self._clock = clock
        self._importers = dict(importers or {})

    async def sync(self, provider_id: str) -> ExternalEvidenceResult:
        provider_key = ProviderId(value=provider_id)
        try:
            manifest = self._registry.manifest(provider_key)
        except ContentIntegrationError as exc:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "provider is not configured"
            ) from exc
        handle = self._access.connected_handle(provider_id, None)
        if (
            not isinstance(handle, CredentialAccessHandle)
            or Permission.READ_PRIVATE not in handle.permissions
        ):
            return ExternalEvidenceResult(
                provider_id=provider_id, inserted=0, duplicates=0, skipped=True
            )
        implementation = self._registry.provider(provider_key)
        inserted = duplicates = 0
        warnings: list[str] = []
        if CapabilityKind.HISTORY in manifest.capabilities:
            added, repeated, failed = await self._sync_capability(
                provider_id,
                handle,
                cast("HistoryCapability", implementation),
                event_type="external_history_view",
            )
            inserted += added
            duplicates += repeated
            if failed:
                warnings.append("history_unavailable")
        if CapabilityKind.SAVED in manifest.capabilities:
            added, repeated, failed = await self._sync_capability(
                provider_id,
                handle,
                cast("SavedCapability", implementation),
                event_type="external_save",
            )
            inserted += added
            duplicates += repeated
            if failed:
                warnings.append("saved_unavailable")
        return ExternalEvidenceResult(
            provider_id=provider_id,
            inserted=inserted,
            duplicates=duplicates,
            skipped=not bool(
                manifest.capabilities & {CapabilityKind.HISTORY, CapabilityKind.SAVED}
            ),
            warnings=tuple(warnings),
        )

    async def sync_all(self) -> tuple[ExternalEvidenceResult, ...]:
        """Best-effort scheduled sync; one provider outage cannot stop its siblings."""

        results: list[ExternalEvidenceResult] = []
        for manifest in self._registry.manifests():
            if not manifest.capabilities & {CapabilityKind.HISTORY, CapabilityKind.SAVED}:
                continue
            with suppress(ApplicationError):
                results.append(await self.sync(manifest.provider_id.value))
        return tuple(results)

    async def import_file(self, provider_id: str, path: Path) -> ExternalEvidenceResult:
        """Import only verified provider export formats through the same normalization."""

        importer = self._importers.get(provider_id)
        if importer is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE,
                "provider has no supported export format",
            )
        parsed = importer(path, self._clock())
        items = parsed.items[:_MAX_IMPORT_ITEMS]
        observations: list[Observation] = []
        skipped = 0
        for item in items:
            try:
                observations.append(
                    self._observation(
                        provider_id=provider_id,
                        ref=item.ref,
                        title=item.title,
                        creator_label=item.creator_label,
                        occurred_at=item.occurred_at,
                        event_type=item.event_type,
                    )
                )
            except ValueError:
                skipped += 1  # untrusted provider text fails per row, never per batch
        inserted, duplicates = await self._record(tuple(observations))
        return ExternalEvidenceResult(
            provider_id=provider_id,
            inserted=inserted,
            duplicates=duplicates,
            ignored=parsed.ignored + len(parsed.items) - len(items) + skipped,
            warnings=parsed.warnings,
        )

    async def _sync_capability(
        self,
        provider_id: str,
        handle: CredentialAccessHandle,
        capability: HistoryCapability | SavedCapability,
        *,
        event_type: str,
    ) -> tuple[int, int, bool]:
        inserted = duplicates = 0
        cursor = None
        for _page_number in range(_MAX_PAGES):
            try:
                request = PageRequest(limit=_PAGE_SIZE, cursor=cursor)
                page: ContentPage[ContentPreview]
                if event_type == "external_history_view":
                    page = await cast("HistoryCapability", capability).history(request, handle)
                else:
                    page = await cast("SavedCapability", capability).saved(request, handle)
            except (ContentIntegrationError, RuntimeError, ValueError):
                return inserted, duplicates, True
            events = []
            for item in page.items:
                try:
                    events.append(
                        self._observation(
                            provider_id=provider_id,
                            ref=item.ref,
                            title=item.title,
                            creator_label=item.creator_label,
                            occurred_at=item.source_timestamp or item.provenance.projected_at,
                            event_type=event_type,
                        )
                    )
                except ValueError:
                    continue  # untrusted provider text fails per row, never per batch
            added, repeated = await self._record(tuple(events))
            inserted += added
            duplicates += repeated
            cursor = page.next_cursor
            if cursor is None:
                break
        return inserted, duplicates, False

    async def _record(self, observations: tuple[Observation, ...]) -> tuple[int, int]:
        inserted = duplicates = 0
        for offset in range(0, len(observations), _PAGE_SIZE):
            receipt = await self._ingress.record_batch(
                observations[offset : offset + _PAGE_SIZE], allowed_event_types=_ALLOWED_EVENTS
            )
            inserted += sum(item.status is RecordStatus.INSERTED for item in receipt.items)
            duplicates += sum(item.status is RecordStatus.DUPLICATE for item in receipt.items)
        return inserted, duplicates

    def _observation(
        self,
        *,
        provider_id: str,
        ref: ContentRef,
        title: str,
        creator_label: str | None,
        occurred_at: datetime,
        event_type: str,
    ) -> Observation:
        identity = f"{event_type}:{provider_id}:{ref.provider_content_id}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
        common = {
            "observation_id": f"obs_{digest}",
            "idempotency_key": identity,
            "occurred_at": occurred_at,
            "received_at": self._clock(),
            "account_id": None,
            "content_ref": ref,
            "provenance": ObservationProvenance(
                producer_id=f"provider.{provider_id}.evidence",
                source=ObservationSource.PROVIDER_IMPORT,
                authenticated=True,
                trust_level=TrustLevel.HIGH,
            ),
            "payload": ExternalContentPayload(
                provider_event_id=ref.provider_content_id,
                title=title,
                creator_label=creator_label,
            ),
        }
        if event_type == "external_history_view":
            return ExternalHistoryViewObservation(**common)
        return ExternalSaveObservation(**common)


def build_external_evidence_job(ingestion: ExternalEvidenceIngestion) -> JobSpec:
    """Schedule conservative, non-overlapping provider evidence ingestion."""

    async def sync() -> None:
        await ingestion.sync_all()

    return JobSpec(
        "content.external_evidence_sync",
        IntervalSchedule(EXTERNAL_EVIDENCE_SYNC_INTERVAL_SECONDS),
        120,
        "network",
        OverlapPolicy.REJECT,
        MissedRunPolicy.SKIP,
        sync,
    )
