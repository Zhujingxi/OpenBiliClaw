"""L4: real recommendation pipeline over a live Bilibili refill."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.record_observation import RecordObservationsCommand
from openbiliclaw.application.refresh_recommendations import RefreshRecommendationsCommand
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.composition.jobs import DEFAULT_PROFILE_ID
from openbiliclaw.core.health import JobResult
from openbiliclaw.core.jobs import JobDecision
from openbiliclaw.observations.models import PreferencePayload, PreferenceStatementObservation
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import PreferenceClaim, PreferenceDimension
from openbiliclaw.understanding.projections import discovery_projection

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.recommendation.models import RecommendationFeedItem

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l4, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data-e2e"


async def _application() -> Application:
    application = build_application(
        validated_settings(_DATA_DIR / "config.e2e.toml"),
        options=BuildOptions(data_dir=_DATA_DIR),
    )
    await application.start()
    return application


async def _connect_anonymous(application: Application) -> None:
    facade = application.services.facade
    assert facade is not None
    await facade.connect_source(
        ConnectSourceCommand(
            idempotency_key=f"e2e:l4:anonymous:{uuid.uuid4().hex}",
            request=AccessRequest(
                provider_id="bilibili",
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            allowed_method_ids=frozenset({"builtin.anonymous"}),
        )
    )


_CONTENT_STATEMENTS = (
    "只提取内容主题偏好。内容主题：Python 编程教程。不要提取风格或语言偏好。标识：{tag}",
    "这是内容主题，不是表达风格。内容主题：Python 实战编程。仅返回 content 维度。标识：{tag}",
    "用户想看的内容类别是 Python 编程课程。维度必须是 content。标识：{tag}",
)


async def _ensure_content_preference(application: Application) -> tuple[str, str]:
    understanding = application.services.understanding
    facade = application.services.facade
    assert understanding is not None
    assert facade is not None
    derived: list[str] = []
    own_claim: PreferenceClaim | None = None
    own_evidence_id = ""
    for statement in _CONTENT_STATEMENTS:
        run = uuid.uuid4().hex
        now = datetime.now(UTC)
        own_evidence_id = f"ev_{run}"
        observation = PreferenceStatementObservation(
            observation_id=f"obs_{run}",
            idempotency_key=f"e2e:l4:preference:{run}",
            occurred_at=now,
            received_at=now,
            account_id="e2e",
            content_ref=None,
            provenance=ObservationProvenance(
                producer_id="host.e2e",
                source=ObservationSource.ASSISTANT,
                authenticated=True,
                trust_level=TrustLevel.HIGH,
            ),
            payload=PreferencePayload(statement=statement.format(tag=run[:8])),
        )
        await facade.record_observations(
            RecordObservationsCommand(
                idempotency_key=f"e2e:l4:batch:{run}",
                observations=(observation,),
                allowed_event_types=frozenset({"preference_statement"}),
            )
        )
        await understanding.process(DEFAULT_PROFILE_ID)
        profile = await understanding.profile(DEFAULT_PROFILE_ID)
        attempt_claims = tuple(
            claim
            for claim in profile.claims
            if isinstance(claim, PreferenceClaim) and own_evidence_id in claim.evidence_ids
        )
        derived.extend(claim.dimension.value for claim in attempt_claims)
        own_claim = next(
            (claim for claim in attempt_claims if claim.dimension is PreferenceDimension.CONTENT),
            None,
        )
        if own_claim is not None:
            break
    if own_claim is None:
        pytest.fail(
            "Kimi derived no content preference after three explicit attempts; "
            f"derived dimensions={derived!r}"
        )

    # The planner consumes only interests[:5]. Remove older content preferences so
    # this run's evidence-backed claim is provably inside that bounded window.
    profile = await understanding.profile(DEFAULT_PROFILE_ID)
    for claim in profile.claims:
        if (
            isinstance(claim, PreferenceClaim)
            and claim.dimension is PreferenceDimension.CONTENT
            and claim.claim_id != own_claim.claim_id
        ):
            await understanding.apply_override(
                DEFAULT_PROFILE_ID,
                claim_id=claim.claim_id,
                operation=OverrideOperation.REMOVE,
                value=None,
            )
    profile = await understanding.profile(DEFAULT_PROFILE_ID)
    interests = discovery_projection(profile).interests[:5]
    assert own_claim.value in interests
    assert any(
        isinstance(claim, PreferenceClaim)
        and claim.claim_id == own_claim.claim_id
        and own_evidence_id in claim.evidence_ids
        for claim in profile.claims
    )
    return own_claim.value, own_evidence_id


async def _refresh(application: Application) -> tuple[RecommendationFeedItem, ...]:
    facade = application.services.facade
    assert facade is not None
    result = await facade.refresh_recommendations(
        RefreshRecommendationsCommand(
            idempotency_key=f"e2e:l4:refresh:{uuid.uuid4().hex}", maximum_items=20
        )
    )
    assert result.decision is JobDecision.RUN
    for _ in range(240):
        await asyncio.sleep(0.25)
        health = await facade.job_health()
        job = next(
            (item for item in health.health.jobs if item.job_id == "recommendation.replenishment"),
            None,
        )
        if job is not None and job.runs_completed:
            assert job.last_result is JobResult.SUCCESS
            break
    else:
        pytest.fail("recommendation replenishment did not complete")
    return (await facade.get_recommendations(20)).items


async def test_real_refill_ranking_reasons_diversity_and_profile_query() -> None:
    application = await _application()
    try:
        await _connect_anonymous(application)
        preference, preference_evidence_id = await _ensure_content_preference(application)
        assert application.resources is not None
        await _refresh(application)
        feed = (await application.services.facade.get_recommendations(20)).items  # type: ignore[union-attr]

        assert feed
        latest_seed = feed[0].selection.seed
        latest = tuple(item for item in feed if item.selection.seed == latest_seed)
        # Interacted candidates (feedback recorded, e.g. by L7 UI runs) are
        # legitimately excluded from later feeds, so a seed's visible ranks
        # are a strictly ascending, duplicate-free subset of 1..n — not
        # necessarily the full 1..n contiguous prefix.
        ranks = tuple(item.selection.rank for item in latest)
        assert ranks == tuple(sorted(set(ranks))), ranks
        assert tuple(item.selection.score for item in latest) == tuple(
            sorted((item.selection.score for item in latest), reverse=True)
        )
        assert all(item.reason.strip() for item in latest)
        assert all(item.selection.contributions for item in latest)
        for item in latest:
            contribution_names = {entry.component for entry in item.selection.contributions}
            assert contribution_names == {"model", "freshness", "novelty"}
            assert item.selection.score == pytest.approx(
                sum(entry.value for entry in item.selection.contributions)
            )

        assert application.resources is not None
        latest_ids = tuple(item.selection.candidate_id for item in latest)
        placeholders = ",".join("?" for _ in latest_ids)
        async with application.resources.database.transaction() as session:
            candidate_rows = await session.fetch_all(
                f"SELECT candidate_json FROM recommendation_candidates "
                f"WHERE candidate_id IN ({placeholders})",
                latest_ids,
            )
        creators = Counter(
            creator
            for row in candidate_rows
            if (creator := json.loads(str(row[0]))["preview"]["creator_label"])
        )
        providers = Counter(item.ref.provider_id.value for item in latest)
        assert max(creators.values(), default=0) <= 1
        assert max(providers.values(), default=0) <= 2
        assert len({item.selection.candidate_id for item in latest}) == len(latest)

        profile = await application.services.understanding.profile(DEFAULT_PROFILE_ID)  # type: ignore[union-attr]
        run_claim = next(
            claim
            for claim in profile.claims
            if isinstance(claim, PreferenceClaim)
            and preference_evidence_id in claim.evidence_ids
            and claim.dimension is PreferenceDimension.CONTENT
        )
        assert run_claim.value == preference
        assert preference in discovery_projection(profile).interests[:5]
        matching_topics = int(
            await application.resources.database.fetch_value(
                "SELECT COUNT(*) FROM recommendation_candidates "
                "WHERE json_extract(candidate_json,'$.topics[0]')=?",
                (preference,),
            )
            or 0
        )
        # Profile influence is truthful but narrow: the only routed preference
        # shapes provider discovery queries and durable candidate topics. The
        # current baseline scorer is intentionally not personalized.
        assert matching_topics > 0
    finally:
        await application.stop()


async def test_recommendation_feed_survives_application_restart() -> None:
    first = await _application()
    try:
        facade = first.services.facade
        assert facade is not None
        before = await facade.get_recommendations(20)
        assert before.items
        identities = tuple(item.selection.recommendation_id for item in before.items)
    finally:
        await first.stop()

    restarted = await _application()
    try:
        facade = restarted.services.facade
        assert facade is not None
        after = await facade.get_recommendations(20)
        assert tuple(item.selection.recommendation_id for item in after.items) == identities
        assert all(item.reason for item in after.items)
    finally:
        await restarted.stop()
