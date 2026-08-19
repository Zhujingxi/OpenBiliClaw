"""Purpose-specific Douyin native projections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)

from .models import DouyinAweme

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> DouyinAweme:
    if not isinstance(content.payload, DouyinAweme):
        raise ValueError("Douyin projection requires Douyin native payload")
    return content.payload


def _timestamp(content: NativeContent) -> datetime:
    return datetime.fromtimestamp(_payload(content).create_time, tz=UTC)


def _provenance(content: NativeContent) -> ProjectionProvenance:
    timestamp = _timestamp(content)
    return ProjectionProvenance(
        ref=content.ref, native_schema_version=content.schema_version, projected_at=timestamp
    )


def preview(content: NativeContent) -> ContentPreview:
    item = _payload(content)
    return ContentPreview(
        ref=content.ref,
        title=item.desc,
        summary=item.desc,
        creator_label=item.author.nickname if item.author else None,
        image_url=item.video.cover_url if item.video else None,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    item = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=item.desc,
        summary=item.desc,
        discovery_reason="douyin:public_search",
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    item = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=item.desc,
        body=item.desc,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    item = _payload(content)
    views = item.statistics.play_count if item.statistics else 0
    return CardData(
        ref=content.ref,
        title=item.desc,
        summary=item.desc,
        badge=f"Douyin · {views} views",
        image_url=item.video.cover_url if item.video else None,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )
