from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from openbiliclaw.access.models import AccessHandle, CredentialAccessHandle, Permission
from openbiliclaw.application.external_evidence import (
    EXTERNAL_EVIDENCE_SYNC_INTERVAL_SECONDS,
    ExternalEvidenceIngestion,
    build_external_evidence_job,
)
from openbiliclaw.composition.external_evidence import youtube_takeout_import
from openbiliclaw.content.integration.capabilities import ContentPage, PageRequest, ProviderCursor
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.content.integration.projections import ContentPreview, ProjectionProvenance
from openbiliclaw.core.jobs import IntervalSchedule
from openbiliclaw.observations.models import (
    ExternalHistoryViewObservation,
    ExternalSaveObservation,
    Observation,
)
from openbiliclaw.observations.provenance import ObservationSource, TrustLevel
from openbiliclaw.observations.service import RecordBatchResult, RecordItemResult, RecordStatus
from openbiliclaw.understanding.service import _project_observation

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 2, tzinfo=UTC)
BILIBILI = ProviderId(value="bilibili")
VIDEO = ContentKind(value="video")


def preview(content_id: str, title: str, occurred_at: datetime = NOW) -> ContentPreview:
    ref = ContentRef(
        provider_id=BILIBILI,
        content_kind=VIDEO,
        provider_content_id=content_id,
        canonical_url=f"https://www.bilibili.com/video/{content_id}",
    )
    return ContentPreview(
        ref=ref,
        title=title,
        summary="bounded summary",
        creator_label="creator",
        source_timestamp=occurred_at,
        provenance=ProjectionProvenance(ref=ref, native_schema_version=1, projected_at=NOW),
    )


class Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def history(self, page: PageRequest, access: object) -> ContentPage[ContentPreview]:
        del access
        cursor = page.cursor.value if page.cursor else None
        self.calls.append(("history", cursor))
        if cursor is None:
            return ContentPage(
                items=(preview("BVhistory1", "History title"),),
                next_cursor=ProviderCursor(provider_id=BILIBILI, value="history-next"),
            )
        return ContentPage(items=(preview("BVhistory2", "Older title"),), next_cursor=None)

    async def saved(self, page: PageRequest, access: object) -> ContentPage[ContentPreview]:
        del access
        cursor = page.cursor.value if page.cursor else None
        self.calls.append(("saved", cursor))
        return ContentPage(
            items=(preview("BVsave1", "Saved title"),) if cursor is None else (),
            next_cursor=None,
        )


class Registry:
    def __init__(self, provider: Provider) -> None:
        self.implementation = provider
        self._manifest = ProviderManifest(
            provider_id=BILIBILI,
            display_name="Bilibili",
            capabilities=frozenset({CapabilityKind.HISTORY, CapabilityKind.SAVED}),
            native_schemas=(),
            availability=ProviderAvailability.AVAILABLE,
        )

    def manifests(self) -> tuple[ProviderManifest, ...]:
        return (self._manifest,)

    def manifest(self, provider_id: ProviderId) -> ProviderManifest:
        assert provider_id == BILIBILI
        return self._manifest

    def provider(self, provider_id: ProviderId) -> object:
        assert provider_id == BILIBILI
        return self.implementation


class Access:
    def __init__(self, connected: bool = True) -> None:
        self.handle = (
            CredentialAccessHandle(
                provider_id="bilibili",
                account_id=None,
                permissions=frozenset({Permission.READ_PRIVATE}),
                credential_ref="cred_" + "a" * 32,
                revision=1,
            )
            if connected
            else None
        )

    def connected_handle(self, provider_id: str, account_id: str | None) -> AccessHandle | None:
        assert provider_id == "bilibili" and account_id is None
        return self.handle


class Ingress:
    def __init__(self) -> None:
        self.events: list[Observation] = []
        self.keys: set[tuple[str, str]] = set()

    async def record_batch(
        self, observations: tuple[Observation, ...], *, allowed_event_types: frozenset[str]
    ) -> RecordBatchResult:
        results: list[RecordItemResult] = []
        for index, event in enumerate(observations):
            typed = cast("ExternalHistoryViewObservation | ExternalSaveObservation", event)
            assert typed.event_type in allowed_event_types
            key = (typed.provenance.producer_id, typed.idempotency_key)
            status = RecordStatus.DUPLICATE if key in self.keys else RecordStatus.INSERTED
            self.keys.add(key)
            self.events.append(typed)
            results.append(
                RecordItemResult(index=index, status=status, observation_id=typed.observation_id)
            )
        return RecordBatchResult(tuple(results))


async def test_sync_normalizes_bounded_pages_with_behavioral_trust_and_idempotency() -> None:
    provider = Provider()
    ingress = Ingress()
    service = ExternalEvidenceIngestion(Registry(provider), Access(), ingress, clock=lambda: NOW)

    first = await service.sync("bilibili")
    second = await service.sync("bilibili")

    assert (first.inserted, first.duplicates, first.skipped) == (3, 0, False)
    assert (second.inserted, second.duplicates, second.skipped) == (0, 3, False)
    assert (
        provider.calls
        == [
            ("history", None),
            ("history", "history-next"),
            ("saved", None),
        ]
        * 2
    )
    events = ingress.events[:3]
    assert isinstance(events[0], ExternalHistoryViewObservation)
    assert isinstance(events[2], ExternalSaveObservation)
    assert all(event.provenance.source is ObservationSource.PROVIDER_IMPORT for event in events)
    assert all(event.provenance.authenticated for event in events)
    assert all(event.provenance.trust_level is TrustLevel.HIGH for event in events)
    assert all(_project_observation(event).trust == 0.6 for event in events)
    assert _project_observation(events[0]).summary == (
        "external history view: History title by creator (bilibili/BVhistory1)"
    )
    assert len({event.idempotency_key for event in events}) == 3


async def test_supervised_sync_job_uses_conservative_named_cadence() -> None:
    provider = Provider()
    service = ExternalEvidenceIngestion(
        Registry(provider), Access(False), Ingress(), clock=lambda: NOW
    )
    job = build_external_evidence_job(service)

    assert job.job_id == "content.external_evidence_sync"
    assert isinstance(job.schedule, IntervalSchedule)
    assert job.schedule.seconds == EXTERNAL_EVIDENCE_SYNC_INTERVAL_SECONDS == 900
    await job.run()
    assert provider.calls == []


async def test_sync_skips_uncredentialed_provider_without_calling_it() -> None:
    provider = Provider()
    service = ExternalEvidenceIngestion(
        Registry(provider), Access(False), Ingress(), clock=lambda: NOW
    )

    result = await service.sync("bilibili")

    assert result.skipped
    assert result.inserted == result.duplicates == 0
    assert provider.calls == []


async def test_youtube_takeout_import_uses_same_observation_normalization(tmp_path: Path) -> None:
    root = tmp_path / "YouTube and YouTube Music"
    (root / "history").mkdir(parents=True)
    (root / "playlists").mkdir()
    (root / "history" / "watch-history.json").write_text(
        json.dumps(
            [
                {
                    "header": "YouTube",
                    "title": "Watched Imported title",
                    "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk",
                    "time": "2025-01-02T03:04:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "playlists" / "Liked videos.csv").write_text(
        "Video ID,Video URL,Video Title\nlmnopqrstuv,https://youtu.be/lmnopqrstuv,Saved import\n",
        encoding="utf-8",
    )
    ingress = Ingress()
    service = ExternalEvidenceIngestion(
        Registry(Provider()),
        Access(False),
        ingress,
        clock=lambda: NOW,
        importers={"youtube": youtube_takeout_import},
    )

    first = await service.import_file("youtube", root)
    second = await service.import_file("youtube", root)

    assert (first.inserted, first.duplicates, first.ignored) == (1, 0, 1)
    assert (second.inserted, second.duplicates, second.ignored) == (0, 1, 1)
    imported = ingress.events[:1]
    assert [event.event_type for event in imported] == ["external_history_view"]
    assert [cast("ContentRef", event.content_ref).provider_content_id for event in imported] == [
        "abcdefghijk"
    ]


async def test_poison_title_skips_one_row_without_aborting_the_sync() -> None:
    class PoisonProvider(Provider):
        async def history(self, page: PageRequest, access: object) -> ContentPage[ContentPreview]:
            del access
            self.calls.append(("history", None))
            return ContentPage(
                items=(
                    preview("BVpoison", "Cookie: Clicker playthrough"),
                    preview("BVfine", "Normal title"),
                ),
                next_cursor=None,
            )

        async def saved(self, page: PageRequest, access: object) -> ContentPage[ContentPreview]:
            del page, access
            return ContentPage(items=(), next_cursor=None)

    ingress = Ingress()
    service = ExternalEvidenceIngestion(
        Registry(PoisonProvider()), Access(), ingress, clock=lambda: NOW
    )

    result = await service.sync("bilibili")

    assert result.inserted == 1
    assert [event.content_ref and event.content_ref.provider_content_id for event in ingress.events] == ["BVfine"]
