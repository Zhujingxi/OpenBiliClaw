"""Purpose-specific V2EX projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)

from .models import V2EXTopic

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent


def _payload(content: NativeContent) -> V2EXTopic:
    if not isinstance(content.payload, V2EXTopic):
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
        summary=p.content,
        creator_label=p.member.username,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def recommendation_candidate(content: NativeContent) -> RecommendationCandidate:
    p = _payload(content)
    return RecommendationCandidate(
        ref=content.ref,
        title=p.title,
        summary=p.content,
        discovery_reason="v2ex:public_api",
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def search_document(content: NativeContent) -> SearchDocument:
    p = _payload(content)
    return SearchDocument(
        ref=content.ref,
        title=p.title,
        body=p.content or p.title,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )


def card_data(content: NativeContent) -> CardData:
    p = _payload(content)
    return CardData(
        ref=content.ref,
        title=p.title,
        summary=p.content,
        badge=f"V2EX · {p.node.title} · {p.reply_count} replies",
        image_url=None,
        source_timestamp=p.published_at,
        provenance=_provenance(content),
    )
