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
    from collections.abc import Callable
    from datetime import datetime

    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> YouTubeVideo:
    if not isinstance(content.payload, YouTubeVideo):
        raise ValueError("wrong native payload")
    return content.payload


def _provenance(content: NativeContent, clock: Callable[[], datetime]) -> ProjectionProvenance:
    return ProjectionProvenance(
        ref=content.ref,
        native_schema_version=content.schema_version,
        projected_at=_payload(content).published_at or clock(),
    )


def preview(content: NativeContent, clock: Callable[[], datetime]) -> ContentPreview:
    p = _payload(content)
    return ContentPreview(
        ref=content.ref,
        title=p.title,
        summary=p.description,
        creator_label=p.channel.name if p.channel else None,
        image_url=p.thumbnail_url,
        source_timestamp=p.published_at,
        provenance=_provenance(content, clock),
    )


def recommendation_candidate(
    content: NativeContent, clock: Callable[[], datetime]
) -> RecommendationCandidate:
    p = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=p.title,
        summary=p.description,
        discovery_reason="youtube:public_api",
        source_timestamp=p.published_at,
        provenance=_provenance(content, clock),
    )


def search_document(content: NativeContent, clock: Callable[[], datetime]) -> SearchDocument:
    p = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=p.title,
        body=p.description or p.title,
        source_timestamp=p.published_at,
        provenance=_provenance(content, clock),
    )


def card_data(content: NativeContent, clock: Callable[[], datetime]) -> CardData:
    p = _payload(content)
    return CardData(
        ref=content.ref,
        title=p.title,
        summary=p.description,
        badge=f"YouTube · {p.duration_seconds}s · {p.view_count} views",
        image_url=p.thumbnail_url,
        source_timestamp=p.published_at,
        provenance=_provenance(content, clock),
    )
