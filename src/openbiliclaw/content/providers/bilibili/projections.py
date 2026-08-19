"""Purpose-specific Bilibili native projections."""

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

from .models import BilibiliArticle, BilibiliVideo

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> BilibiliVideo | BilibiliArticle:
    if not isinstance(content.payload, BilibiliVideo | BilibiliArticle):
        raise ValueError("Bilibili projection requires Bilibili native payload")
    return content.payload


def _timestamp(content: NativeContent) -> datetime:
    return datetime.fromtimestamp(_payload(content).published_at, tz=UTC)


def _provenance(content: NativeContent) -> ProjectionProvenance:
    timestamp = _timestamp(content)
    return ProjectionProvenance(
        ref=content.ref,
        native_schema_version=content.schema_version,
        # Deterministic native-source time: no wall-clock data enters snapshots/cache keys.
        projected_at=timestamp,
    )


def preview(content: NativeContent) -> ContentPreview:
    payload = _payload(content)
    return ContentPreview(
        ref=content.ref,
        title=payload.title,
        summary=payload.description,
        creator_label=payload.creator.name if payload.creator else None,
        image_url=payload.cover_url,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    payload = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=payload.title,
        summary=payload.description,
        discovery_reason="bilibili:public_feed",
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    payload = _payload(content)
    body = payload.body if isinstance(payload, BilibiliArticle) else payload.description
    return SearchDocument(
        ref=content.ref,
        title=payload.title,
        body=body or payload.title,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    payload = _payload(content)
    return CardData(
        ref=content.ref,
        title=payload.title,
        summary=payload.description,
        badge=f"Bilibili · {payload.stats.views} views",
        image_url=payload.cover_url,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )
