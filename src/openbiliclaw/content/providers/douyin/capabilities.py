"""Douyin native conversion and projections; session-bound reads are not exposed."""

from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    RecommendationCandidate,
    SearchDocument,
)

from . import projections
from .manifest import DOUYIN_ID, SHORT_VIDEO_KIND
from .models import DouyinAweme


class DouyinProvider:
    """Projection-only until a replayable non-browser access method exists."""

    def __repr__(self) -> str:
        return "DouyinProvider(credentials=<inaccessible>)"

    def native_from_model(self, item: DouyinAweme) -> NativeContent:
        return NativeContent(
            ref=ContentRef(
                provider_id=DOUYIN_ID,
                content_kind=SHORT_VIDEO_KIND,
                provider_content_id=item.aweme_id,
                canonical_url=f"https://www.douyin.com/video/{item.aweme_id}",
            ),
            schema_version=1,
            payload=item,
        )

    def preview(self, content: NativeContent) -> ContentPreview:
        return projections.preview(content)

    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate:
        return projections.recommendation_candidate(content)

    def search_document(self, content: NativeContent) -> SearchDocument:
        return projections.search_document(content)

    def card_data(self, content: NativeContent) -> CardData:
        return projections.card_data(content)
