"""Purpose-specific RedNote native projections."""

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

from .models import RednoteNote

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> RednoteNote:
    if not isinstance(content.payload, RednoteNote):
        raise ValueError("RedNote projection requires RedNote native payload")
    return content.payload


def _timestamp(content: NativeContent) -> datetime:
    return datetime.fromtimestamp(_payload(content).published_at, tz=UTC)


def _provenance(content: NativeContent) -> ProjectionProvenance:
    timestamp = _timestamp(content)
    return ProjectionProvenance(
        ref=content.ref, native_schema_version=content.schema_version, projected_at=timestamp
    )


def preview(content: NativeContent) -> ContentPreview:
    item = _payload(content)
    return ContentPreview(
        ref=content.ref,
        title=item.title,
        summary=item.description,
        creator_label=item.author.nickname if item.author else None,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    item = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=item.title,
        summary=item.description,
        discovery_reason="rednote:observed_public_note",
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    item = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=item.title,
        body=item.description or item.title,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    item = _payload(content)
    return CardData(
        ref=content.ref,
        title=item.title,
        summary=item.description,
        badge=f"RedNote · {item.likes} likes",
        image_url=item.cover_url,
        source_timestamp=_timestamp(content),
        provenance=_provenance(content),
    )
