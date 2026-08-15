"""Provider/channel provenance survives candidate persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.content.integration.capabilities import FeedQuery
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.projections import ContentPreview, ProjectionProvenance
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import DEFAULT_MIGRATIONS, SchemaMigrator
from openbiliclaw.recommendation.models import (
    Candidate,
    DiscoveryProvenance,
    candidate_identity,
)
from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_feed_query_provenance_survives_candidate_round_trip(tmp_path: Path) -> None:
    query = FeedQuery(feed_id="popular")
    ref = ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="video"),
        provider_content_id="BV1",
        canonical_url="https://www.bilibili.com/video/BV1",
    )
    preview = ContentPreview(
        ref=ref,
        title="Popular video",
        summary="summary",
        source_timestamp=NOW,
        provenance=ProjectionProvenance(ref=ref, native_schema_version=1, projected_at=NOW),
    )
    candidate = Candidate(
        candidate_id=candidate_identity(ref, "provider.feed", query.feed_id or "default"),
        preview=preview,
        provenance=DiscoveryProvenance(
            strategy_id="provider.feed",
            query_key=query.feed_id or "default",
            provider=ref.provider_id.value,
            channel=query.feed_id,
            discovered_at=NOW,
        ),
        expires_at=NOW + timedelta(days=1),
    )

    path = tmp_path / "provenance.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        repository = SqliteRecommendationRepository(database)
        assert await repository.add_candidate(candidate)
        loaded = await repository.load(candidate.candidate_id)
        assert (loaded.provenance.provider, loaded.provenance.channel) == (
            ref.provider_id.value,
            query.feed_id,
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_backfills_provider_for_existing_candidates(tmp_path: Path) -> None:
    """Adding required provenance must not make pre-channel candidate rows unreadable."""

    path = tmp_path / "upgrade.db"
    await SchemaMigrator(path, migrations=DEFAULT_MIGRATIONS[:7]).migrate()
    database = SqliteDatabase(path)
    await database.open()
    ref = ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="video"),
        provider_content_id="old",
        canonical_url="https://www.bilibili.com/video/old",
    )
    preview = ContentPreview(
        ref=ref,
        title="Old candidate",
        summary="summary",
        source_timestamp=NOW,
        provenance=ProjectionProvenance(ref=ref, native_schema_version=1, projected_at=NOW),
    )
    current = Candidate(
        candidate_id=candidate_identity(ref, "search", "old"),
        preview=preview,
        provenance=DiscoveryProvenance(
            strategy_id="search",
            query_key="old",
            provider="bilibili",
            channel=None,
            discovered_at=NOW,
        ),
        expires_at=NOW + timedelta(days=1),
    )
    legacy = current.model_dump(mode="json")
    legacy_provenance = cast("dict[str, object]", legacy["provenance"])
    del legacy_provenance["provider"]
    del legacy_provenance["channel"]
    async with database.transaction() as session:
        await session.execute(
            "INSERT INTO recommendation_candidates"
            " (candidate_id, state, candidate_json, created_at) VALUES (?, ?, ?, ?)",
            (
                current.candidate_id,
                current.state.value,
                json.dumps(legacy),
                NOW.isoformat(),
            ),
        )
    await database.close()

    assert await SchemaMigrator(path).migrate() == len(DEFAULT_MIGRATIONS)
    assert await SchemaMigrator(path).migrate() == len(DEFAULT_MIGRATIONS)  # idempotent
    reopened = SqliteDatabase(path)
    await reopened.open()
    try:
        loaded = await SqliteRecommendationRepository(reopened).load(current.candidate_id)
        assert loaded.provenance.provider == "bilibili"
        assert loaded.provenance.channel is None
    finally:
        await reopened.close()
