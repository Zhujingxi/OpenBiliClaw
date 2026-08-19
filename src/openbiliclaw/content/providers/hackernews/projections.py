"""Purpose-specific Hacker News projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)

from .models import HackerNewsItem

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> HackerNewsItem:
    if not isinstance(content.payload, HackerNewsItem):
        raise ValueError("wrong native payload")
    return content.payload


def _provenance(content: NativeContent) -> ProjectionProvenance:
    return ProjectionProvenance(
        ref=content.ref,
        native_schema_version=content.schema_version,
        projected_at=_payload(content).published_at,
    )


def preview(content: NativeContent) -> ContentPreview:
    item = _payload(content)
    return ContentPreview(
        ref=content.ref,
        title=item.title,
        summary=item.body,
        creator_label=item.author,
        source_timestamp=item.published_at,
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    item = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=item.title,
        summary=item.body,
        discovery_reason="hackernews:top",
        source_timestamp=item.published_at,
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    item = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=item.title,
        body=item.body or item.title,
        source_timestamp=item.published_at,
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    item = _payload(content)
    return CardData(
        ref=content.ref,
        title=item.title,
        summary=item.body,
        badge=f"Hacker News · {item.score} points · {item.comment_count} comments",
        image_url=None,
        source_timestamp=item.published_at,
        provenance=_provenance(content),
    )
