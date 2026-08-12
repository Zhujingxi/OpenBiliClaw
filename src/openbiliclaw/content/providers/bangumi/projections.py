"""Purpose-specific Bangumi projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)

from .models import BangumiSubject

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> BangumiSubject:
    if not isinstance(content.payload, BangumiSubject):
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
        summary=p.summary,
        creator_label=p.creator,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    p = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=p.title,
        summary=p.summary,
        discovery_reason="bangumi:public_api",
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    p = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=p.title,
        body=p.summary or p.title,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    p = _payload(content)
    return CardData(
        ref=content.ref,
        title=p.title,
        summary=p.summary,
        badge=f"Bangumi · {p.score:.1f}",
        image_url=p.image_url,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )
