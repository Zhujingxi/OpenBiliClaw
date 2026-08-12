from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.projections import ContentPreview, ProjectionProvenance
from openbiliclaw.recommendation.evaluation.prefilter import (
    normalize_and_prefilter,
    persist_rejections,
)
from openbiliclaw.recommendation.expression.agent import ExpressedItem, ExpressionBatch
from openbiliclaw.recommendation.expression.service import ExpressionService
from openbiliclaw.recommendation.models import (
    Candidate,
    DiscoveryProvenance,
    RejectionReason,
    ScoreContribution,
    SelectionRecord,
    candidate_identity,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def candidate(identity: str, **changes: object) -> Candidate:
    ref = ContentRef(
        provider_id=ProviderId(value="demo"),
        content_kind=ContentKind(value="video"),
        provider_content_id=identity,
        canonical_url=f"https://example.com/{identity}",
    )
    provenance = DiscoveryProvenance(strategy_id="test", query_key=identity, discovered_at=NOW)
    value = Candidate(
        candidate_id=candidate_identity(ref, "test", identity),
        preview=ContentPreview(
            ref=ref,
            title=identity,
            summary="summary",
            source_timestamp=NOW,
            provenance=ProjectionProvenance(ref=ref, native_schema_version=1, projected_at=NOW),
        ),
        provenance=provenance,
        expires_at=NOW + timedelta(days=1),
    )
    return value.model_copy(update=changes)


def test_prefilter_accepts_and_covers_every_deterministic_rejection() -> None:
    good = candidate("good")
    variants = (
        candidate(
            "malformed", preview=candidate("malformed").preview.model_copy(update={"title": ""})
        ),
        candidate("duplicate").model_copy(update={"preview": candidate("good").preview}),
        candidate("seen"),
        candidate("blocked"),
        candidate("stale", expires_at=NOW),
        candidate("inaccessible", accessible=False),
        candidate("unsupported", supported=False),
        candidate(
            "avoid", preview=candidate("avoid").preview.model_copy(update={"summary": "spoiler"})
        ),
    )
    accepted, rejected = normalize_and_prefilter(
        (good, *variants),
        seen_ids=frozenset({variants[2].candidate_id}),
        blocked_urls=frozenset({variants[3].preview.ref.canonical_url}),
        avoidances=("spoiler",),
        now=NOW,
    )
    assert len(accepted) == 1
    assert {reason.value for _, reason in rejected} == {
        "malformed",
        "duplicate",
        "seen",
        "blocked",
        "stale",
        "inaccessible",
        "unsupported",
        "avoidance",
    }


@pytest.mark.asyncio
async def test_persist_rejections_transitions_and_saves_records() -> None:
    rejected_candidate = candidate("reject")
    inventory = type(
        "Inventory",
        (),
        {
            "load": AsyncMock(return_value=rejected_candidate),
            "transition": AsyncMock(),
        },
    )()
    evaluations = type("Evaluations", (), {"save_rejection": AsyncMock()})()
    records = await persist_rejections(
        inventory,
        evaluations,
        ((rejected_candidate, RejectionReason.BLOCKED),),
        now=NOW,
    )
    assert records[0].reason is RejectionReason.BLOCKED
    inventory.transition.assert_awaited_once()
    evaluations.save_rejection.assert_awaited_once()


@pytest.mark.asyncio
async def test_expression_uses_valid_model_output_and_falls_back() -> None:
    selection = SelectionRecord(
        recommendation_id="rec_" + "1" * 32,
        candidate_id="cand_" + "2" * 32,
        rank=1,
        score=0.8,
        contributions=(ScoreContribution(component="base", value=0.8),),
        selected_at=NOW,
        seed=1,
    )

    async def generate(items: tuple[SelectionRecord, ...]) -> tuple[ExpressionBatch, str]:
        return ExpressionBatch(
            items=(
                ExpressedItem(
                    recommendation_id=items[0].recommendation_id, reason="Because", tone="warm"
                ),
            )
        ), "model"

    expressed = await ExpressionService(generate, lambda: NOW).express((selection,))
    fallback = await ExpressionService(None, lambda: NOW).express((selection,))
    assert expressed[0].model_instance == "model"
    assert fallback[0].tone == "neutral"
