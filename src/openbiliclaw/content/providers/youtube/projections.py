"""Purpose-specific YouTube projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)

from .models import YouTubeVideo

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> YouTubeVideo:
    if not isinstance(content.payload, YouTubeVideo):
        raise ValueError("wrong native payload")
    return content.payload


def _provenance(content: NativeContent) -> ProjectionProvenance:
    return ProjectionProvenance(
        ref=content.ref,
        native_schema_version=content.schema_version,
        projected_at=_payload(content).published_at,
    )


def preview(content: NativeContent) -> ContentPreview:
    p = _payload(content)
    return ContentPreview(
        ref=content.ref,
        title=p.title,
        summary=p.description,
        creator_label=p.channel.name if p.channel else None,
        image_url=p.thumbnail_url,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    p = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=p.title,
        summary=p.description,
        discovery_reason="youtube:public_api",
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    p = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=p.title,
        body=p.description or p.title,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    p = _payload(content)
    return CardData(
        ref=content.ref,
        title=p.title,
        summary=p.description,
        badge=f"YouTube · {p.duration_seconds}s · {p.view_count} views",
        image_url=p.thumbnail_url,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )
