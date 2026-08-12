"""RedNote native/projection support with session-bound reads unavailable."""

from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    RecommendationCandidate,
    SearchDocument,
)

from . import projections
from .manifest import NOTE_KIND, REDNOTE_ID
from .models import RednoteNote


class RednoteProvider:
    """Projection-only provider until a replayable AccessMethod exists."""

    def __repr__(self) -> str:
        return "RednoteProvider(credentials=<inaccessible>)"

    def native_from_model(self, item: RednoteNote) -> NativeContent:
        return NativeContent(
            ref=ContentRef(
                provider_id=REDNOTE_ID,
                content_kind=NOTE_KIND,
                provider_content_id=item.note_id,
                canonical_url=f"https://www.xiaohongshu.com/explore/{item.note_id}",
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
