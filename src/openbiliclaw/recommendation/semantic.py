"""Embedding-backed candidate recall for the adjacent exploration arm."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex, EmbeddingMatch
    from openbiliclaw.understanding.projections import RecommendationProfile

    from .models import Candidate

# Versioned deterministic recall defaults; statistical allocation still learns the arm magnitude.
ADJACENT_MIN_SIMILARITY_V1 = 0.70
TOP_INTEREST_MATCH_V1 = 0.85


def candidate_embedding_text(candidate: Candidate) -> str:
    """Return one deterministic bounded metadata fragment for candidate indexing."""

    return "\n".join(
        part
        for part in (
            candidate.preview.title,
            candidate.preview.summary,
            " ".join(candidate.topics),
        )
        if part.strip()
    )


async def adjacent_recall(
    index: EmbeddingIndex,
    profile: RecommendationProfile,
    *,
    limit: int,
    adjacent_min_similarity: float = ADJACENT_MIN_SIMILARITY_V1,
    top_interest_match: float = TOP_INTEREST_MATCH_V1,
) -> tuple[EmbeddingMatch, ...]:
    """Recall candidates near weak claims while excluding established-interest matches."""

    if not 1 <= limit <= 20:
        raise ValueError("adjacent recall limit must be between 1 and 20")
    weak = tuple(item for item in profile.embedding_claims if not item.top_interest)
    top = tuple(item for item in profile.embedding_claims if item.top_interest)
    if not weak:
        return ()
    recall_limit = min(100, limit * 5)
    top_scores: dict[str, float] = {}
    for claim in top:
        vector = await index.vector("claim", claim.ref_id)
        if vector is None:
            continue
        for ref_id, score in await index.query(
            vector=vector, kinds=("candidate",), limit=recall_limit
        ):
            top_scores[ref_id] = max(top_scores.get(ref_id, -1.0), score)

    adjacent_scores: dict[str, float] = {}
    for claim in weak:
        vector = await index.vector("claim", claim.ref_id)
        if vector is None:
            continue
        for ref_id, score in await index.query(
            vector=vector, kinds=("candidate",), limit=recall_limit
        ):
            if (
                score < adjacent_min_similarity
                or top_scores.get(ref_id, -1.0) >= top_interest_match
            ):
                continue
            adjacent_scores[ref_id] = max(adjacent_scores.get(ref_id, -1.0), score)
    return tuple(sorted(adjacent_scores.items(), key=lambda item: (-item[1], item[0]))[:limit])
