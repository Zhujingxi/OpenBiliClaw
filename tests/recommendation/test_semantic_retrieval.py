from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.ai.providers.embeddings import (
    EmbeddingModelInfo,
    EmbeddingResult,
    EmbeddingUsage,
)
from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.semantic import adjacent_recall
from openbiliclaw.understanding.profile import (
    CanonicalProfile,
    EmergingInterestClaim,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.projections import recommendation_projection

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.ai.providers.embeddings import Vector

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class FakeEmbeddings:
    def __init__(self, vectors: dict[str, Vector]) -> None:
        self.model = EmbeddingModelInfo(
            provider="test", model="semantic-v1", dimensions=2, normalized=True, version="1"
        )
        self.vectors = vectors

    async def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple(self.vectors[text] for text in texts),
            usage=EmbeddingUsage(requests=1, input_tokens=len(texts)),
            model=self.model,
        )

    async def embed_query(self, text: str) -> Vector:
        return self.vectors[text]


@pytest.mark.asyncio
async def test_adjacent_recall_selects_candidate_near_weak_but_not_top_claim(
    tmp_path: Path,
) -> None:
    top = StableInterestClaim(
        claim_id=claim_id("stable_interest", "science"),
        value="science",
        confidence=0.95,
        fresh_at=NOW,
        evidence_ids=("ev_" + "1" * 32,),
    )
    weak = EmergingInterestClaim(
        claim_id=claim_id("emerging_interest", "robotics"),
        value="robotics",
        confidence=0.4,
        fresh_at=NOW,
        evidence_ids=("ev_" + "2" * 32,),
    )
    provider = FakeEmbeddings(
        {
            # Claims 37 degrees apart so the top-exclusion branch is reachable:
            # candidate-top clears the weak threshold AND the top threshold.
            "science": (1.0, 0.0),
            "robotics": (0.8, 0.6),
            "robotics project": (0.35, 0.937),
            "general science": (0.98, 0.199),
        }
    )
    path = tmp_path / "semantic.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        index = EmbeddingIndex(database, provider, provider.model, clock=lambda: NOW)
        await index.upsert("claim", top.claim_id, top.value)
        await index.upsert("claim", weak.claim_id, weak.value)
        await index.upsert("candidate", "candidate-adjacent", "robotics project")
        await index.upsert("candidate", "candidate-top", "general science")
        profile = recommendation_projection(
            CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(top, weak))
        )

        assert profile.embedding_claims[0].top_interest
        assert not profile.embedding_claims[1].top_interest
        recalled = await adjacent_recall(index, profile, limit=5)
        assert [ref_id for ref_id, _score in recalled] == ["candidate-adjacent"]
        # candidate-top passes the weak threshold (0.90) but is excluded by the
        # top-interest threshold (0.98 >= 0.85): adjacent never leaks core interests.
        assert recalled[0][1] == pytest.approx(0.842, abs=1e-3)
    finally:
        await database.close()
