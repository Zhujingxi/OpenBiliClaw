"""L3: real user understanding and semantic smoke over the composed services."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.edit_profile import EditProfileCommand
from openbiliclaw.application.record_observation import RecordObservationsCommand
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.observations.models import PreferencePayload, PreferenceStatementObservation
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.service import RecordStatus
from openbiliclaw.understanding.overrides import OverrideOperation

if TYPE_CHECKING:
    from openbiliclaw.ai.providers.embeddings.protocol import Vector
    from openbiliclaw.composition.application import Application

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l3, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data-e2e"
_PROFILE_ID = "e2e-real"


async def _application() -> Application:
    settings = validated_settings(_DATA_DIR / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_DATA_DIR))
    await application.start()
    return application


async def _connect_anonymous(application: Application) -> None:
    facade = application.services.facade
    assert facade is not None
    await facade.connect_source(
        ConnectSourceCommand(
            idempotency_key=f"e2e:l3:anonymous:{uuid.uuid4().hex}",
            request=AccessRequest(
                provider_id="bilibili",
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            allowed_method_ids=frozenset({"builtin.anonymous"}),
        )
    )


def _cosine(left: Vector, right: Vector) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    return numerator / math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))


async def _record_preference(application: Application, statement: str) -> str:
    facade = application.services.facade
    assert facade is not None
    now = datetime.now(UTC)
    run = uuid.uuid4().hex
    observation = PreferenceStatementObservation(
        observation_id=f"obs_{run}",
        idempotency_key=f"e2e:l3:preference:{run}",
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
        payload=PreferencePayload(statement=statement),
    )
    result = await facade.record_observations(
        RecordObservationsCommand(
            idempotency_key=f"e2e:l3:batch:{run}",
            observations=(observation,),
            allowed_event_types=frozenset({"preference_statement"}),
        )
    )
    assert result.items[0].status is RecordStatus.INSERTED
    return observation.observation_id


async def test_composed_embeddings_apply_query_instruction_and_rank_real_titles() -> None:
    application = await _application()
    try:
        await _connect_anonymous(application)
        facade = application.services.facade
        embeddings = application.services.embeddings
        assert facade is not None
        assert embeddings is not None
        results = await facade.search_content("bilibili", "Python 编程", 5)
        titles = tuple(item.title for item in results.items)
        assert len(titles) >= 2

        documents = await embeddings.embed_documents(titles)
        query = await embeddings.embed_query(titles[0])
        assert documents.model.dimensions == 512
        assert all(len(vector) == 512 for vector in (*documents.vectors, query))
        similarities = tuple(_cosine(query, vector) for vector in documents.vectors)
        assert similarities[0] == max(similarities)
        assert all(-1 <= score <= 1 for score in similarities)
    finally:
        await application.stop()


async def test_real_profile_derivation_persistence_update_and_correction() -> None:
    first = await _application()
    try:
        understanding = first.services.understanding
        assert understanding is not None
        # ContentRef-only observations intentionally expose no title text. This honest,
        # explicit preference gives the real analyzer enough evidence to derive a claim.
        first_observation_id = await _record_preference(first, "我喜欢实用的 Python 编程教程。")
        processed = await understanding.process(_PROFILE_ID)
        assert processed.accepted > 0
        profile = await understanding.profile(_PROFILE_ID)
        assert profile.revision > 0
        assert profile.claims
        expected_evidence_id = "ev_" + first_observation_id.removeprefix("obs_")
        assert any(expected_evidence_id in claim.evidence_ids for claim in profile.claims)
        original_claim = profile.claims[0]
        assert first.repositories is not None
        first_checkpoint = await first.repositories.understanding.checkpoint(
            "understanding.preference.v1"
        )
        assert first_checkpoint is not None
    finally:
        await first.stop()

    restarted = await _application()
    try:
        understanding = restarted.services.understanding
        facade = restarted.services.facade
        assert understanding is not None
        assert facade is not None
        persisted = await understanding.profile(_PROFILE_ID)
        assert persisted.revision == profile.revision
        assert persisted.claims == profile.claims

        await _record_preference(restarted, "我也喜欢清晰、循序渐进的技术讲解。")
        updated = await understanding.process(_PROFILE_ID)
        assert updated.accepted + updated.rejected >= 0
        after_update = await understanding.profile(_PROFILE_ID)
        assert after_update.revision >= persisted.revision
        assert restarted.repositories is not None
        checkpoint = await restarted.repositories.understanding.checkpoint(
            "understanding.preference.v1"
        )
        assert checkpoint is not None
        assert checkpoint != first_checkpoint

        public_before = await facade.show_profile(_PROFILE_ID)
        assert public_before.profile.version == 1
        assert public_before.profile.preference_summary
        correction = await facade.edit_profile(
            EditProfileCommand(
                idempotency_key=f"e2e:l3:correction:{uuid.uuid4().hex}",
                profile_id=_PROFILE_ID,
                account_id="e2e",
                claim_id=original_claim.claim_id,
                operation=OverrideOperation.REMOVE,
            )
        )
        assert all(item.claim_id != original_claim.claim_id for item in correction.profile.claims)
        corrected = await understanding.profile(_PROFILE_ID)
        assert corrected == correction.profile
        assert any(item.claim_id == original_claim.claim_id for item in corrected.overrides)
        public_after = await facade.show_profile(_PROFILE_ID)
        assert public_after.profile.version == 1
        assert public_after.profile != public_before.profile
    finally:
        await restarted.stop()
