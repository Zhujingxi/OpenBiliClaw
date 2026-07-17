"""Focused Task 21b tests for explicit optimistic profile editing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from openbiliclaw.api.dependencies import require_access
from openbiliclaw.api.routers.profile import router as profile_router
from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.profile.domain import (
    ProfileEdit,
    ProfileFacet,
    ProfileFacetEdit,
    ProfileFacetReference,
    ProfileSnapshot,
)
from openbiliclaw.features.profile.service import ProfileService, StaleProfileRevisionError
from openbiliclaw.infrastructure.database.base import (
    DatabaseSettings,
    create_engine_and_session,
)
from openbiliclaw.infrastructure.database.models import ActivityEventModel, ProfileRevisionModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
EDITED_AT = datetime(2026, 7, 17, 13, 30, tzinfo=UTC)
PROFILE_ID = UUID("00000000-0000-0000-0000-000000000320")
OLD_EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000321")


def _url(path: Path) -> str:
    return f"sqlite:///{path}"


def _database(tmp_path: Path) -> tuple[Engine, sessionmaker[Session]]:
    path = tmp_path / "profile-edit.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", _url(path))
    command.upgrade(config, "head")
    return create_engine_and_session(DatabaseSettings(url=_url(path)))


def _seed_profile(session_factory: sessionmaker[Session]) -> ProfileSnapshot:
    event = ActivityEvent(
        id=OLD_EVIDENCE_ID,
        source_id="local",
        kind=ActivityKind.PROFILE_OVERRIDE,
        title="Clickbait",
        metadata={"facet": "avoidances", "value": "Clickbait", "weight": -1.0},
        occurred_at=NOW,
    )
    snapshot = ProfileSnapshot(
        id=PROFILE_ID,
        revision=0,
        narrative="Old narrative",
        facets=(
            ProfileFacet(
                name="avoidances",
                value="Clickbait",
                weight=-1,
                confidence=0,
                evidence_ids=(OLD_EVIDENCE_ID,),
                overridden=True,
            ),
        ),
        confidence=1,
        created_at=NOW,
    )
    with UnitOfWork(session_factory) as uow:
        uow.activities.add(event)
        uow.profiles.append(snapshot, expected_revision=None)
        uow.commit()
    return snapshot


def test_profile_edit_normalizes_clamps_and_deduplicates_deterministically() -> None:
    edit = ProfileEdit(
        expected_revision=0,
        narrative="  Explicit narrative  ",
        upserts=(
            ProfileFacetEdit(name="interests", value="  Python  ", weight=5),
            ProfileFacetEdit(name="interests", value="python", weight=5),
        ),
        removals=(
            ProfileFacetReference(name="avoidances", value="  Noise "),
            ProfileFacetReference(name="avoidances", value="noise"),
        ),
    )

    assert edit.narrative == "Explicit narrative"
    assert edit.upserts == (ProfileFacetEdit(name="interests", value="Python", weight=1),)
    assert edit.removals == (ProfileFacetReference(name="avoidances", value="Noise"),)
    with pytest.raises(ValidationError, match="cannot remove and upsert"):
        ProfileEdit(
            expected_revision=0,
            upserts=(ProfileFacetEdit(name="values", value="Evidence", weight=1),),
            removals=(ProfileFacetReference(name="values", value="evidence"),),
        )


def test_explicit_edit_creates_one_revision_and_high_confidence_evidence(
    tmp_path: Path,
) -> None:
    engine, session_factory = _database(tmp_path)
    _seed_profile(session_factory)
    service = ProfileService(cast("Any", lambda: UnitOfWork(session_factory)))
    edit = ProfileEdit(
        expected_revision=0,
        narrative="",
        upserts=(
            ProfileFacetEdit(name="interests", value="Typed APIs", weight=0.8),
            ProfileFacetEdit(name="source_affinities", value="Bilibili", weight=2),
        ),
        removals=(ProfileFacetReference(name="avoidances", value="clickbait"),),
    )

    updated = service.edit(edit)

    assert updated.revision == 1
    assert updated.narrative == ""
    assert [(facet.name, facet.value, facet.weight) for facet in updated.facets] == [
        ("interests", "Typed APIs", 0.8),
        ("source_affinities", "Bilibili", 1.0),
    ]
    assert all(facet.overridden and facet.confidence == 1 for facet in updated.facets)
    assert all(len(facet.evidence_ids) == 1 for facet in updated.facets)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProfileRevisionModel)) == 2
        evidence = session.scalars(
            select(ActivityEventModel).where(
                ActivityEventModel.kind == ActivityKind.PROFILE_OVERRIDE.value
            )
        ).all()
    assert len(evidence) == 2
    assert {str(facet.evidence_ids[0]) for facet in updated.facets} == {evidence[1].id}
    engine.dispose()


def test_explicit_edit_persists_a_fresh_aware_utc_revision_timestamp(
    tmp_path: Path,
) -> None:
    engine, session_factory = _database(tmp_path)
    original = _seed_profile(session_factory)
    service = ProfileService(
        cast("Any", lambda: UnitOfWork(session_factory)),
        clock=lambda: EDITED_AT,
    )

    updated = service.edit(
        ProfileEdit(
            expected_revision=0,
            upserts=(ProfileFacetEdit(name="values", value="Evidence", weight=1),),
        )
    )

    assert updated.created_at == EDITED_AT
    assert updated.created_at != original.created_at
    assert updated.created_at.utcoffset() == timedelta(0)
    with UnitOfWork(session_factory) as uow:
        durable = uow.profiles.latest()
    assert durable is not None
    assert durable.created_at == EDITED_AT
    assert durable.revision == 1
    assert durable.facets[-1].evidence_ids == updated.facets[-1].evidence_ids
    engine.dispose()


def test_explicit_edit_conflict_and_commit_failure_leave_no_partial_evidence(
    tmp_path: Path,
) -> None:
    engine, session_factory = _database(tmp_path)
    _seed_profile(session_factory)
    service = ProfileService(cast("Any", lambda: UnitOfWork(session_factory)))
    stale = ProfileEdit(
        expected_revision=7,
        upserts=(ProfileFacetEdit(name="values", value="Evidence", weight=1),),
    )
    with pytest.raises(StaleProfileRevisionError):
        service.edit(stale)

    class FailingCommitUnitOfWork(UnitOfWork):
        def commit(self) -> None:
            self.session.flush()
            raise RuntimeError("simulated commit failure")

    rollback_service = ProfileService(cast("Any", lambda: FailingCommitUnitOfWork(session_factory)))
    valid = stale.model_copy(update={"expected_revision": 0})
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        rollback_service.edit(valid)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProfileRevisionModel)) == 1
        assert session.scalar(select(func.count()).select_from(ActivityEventModel)) == 1
    engine.dispose()


def test_profile_patch_endpoint_delegates_the_typed_edit() -> None:
    edit = ProfileEdit(
        expected_revision=None,
        upserts=(ProfileFacetEdit(name="style_preferences", value="Concise", weight=1),),
    )
    expected = ProfileSnapshot(revision=0)

    class ProfilePort:
        received: ProfileEdit | None = None

        def edit(self, payload: ProfileEdit) -> ProfileSnapshot:
            self.received = payload
            return expected

    port = ProfilePort()
    app = FastAPI()
    app.state.container = SimpleNamespace(profile=port)
    app.dependency_overrides[require_access] = lambda: None
    app.include_router(profile_router, prefix="/api/v1")

    response = TestClient(app).patch("/api/v1/profile", json=edit.model_dump(mode="json"))

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert port.received == edit
