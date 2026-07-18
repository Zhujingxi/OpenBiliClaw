"""Real repository/API regression for read-side feedback ranking."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.features.feed.domain import (
    CandidateAssessment,
    ContentItem,
    FeedEntry,
)
from openbiliclaw.features.profile.domain import ProfileSnapshot
from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.infrastructure.database.base import create_engine_and_session
from openbiliclaw.infrastructure.database.uow import UnitOfWork

ROOT = Path(__file__).parents[2]
HIGH_ID = UUID("00000000-0000-0000-0000-00000000e201")
LOW_ID = UUID("00000000-0000-0000-0000-00000000e202")
HIGH_ASSESSMENT_ID = UUID("00000000-0000-0000-0000-00000000e211")
LOW_ASSESSMENT_ID = UUID("00000000-0000-0000-0000-00000000e212")
PROFILE_ID = UUID("00000000-0000-0000-0000-00000000e220")
CLAMPED_ID = UUID("00000000-0000-0000-0000-00000000e203")
ZERO_ID = UUID("00000000-0000-0000-0000-00000000e204")
CLAMPED_ASSESSMENT_ID = UUID("00000000-0000-0000-0000-00000000e213")
ZERO_ASSESSMENT_ID = UUID("00000000-0000-0000-0000-00000000e214")


def _seed_ranked_feed(database_url: str) -> None:
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=database_url))
    high = ContentItem(
        id=HIGH_ID,
        source_id="zhihu",
        external_id="high",
        url="https://www.zhihu.com/question/high",
        title="Initially highest",
    )
    low = ContentItem(
        id=LOW_ID,
        source_id="zhihu",
        external_id="low",
        url="https://www.zhihu.com/question/low",
        title="Initially second",
    )
    with UnitOfWork(session_factory) as uow:
        uow.content.add(high)
        uow.content.add(low)
        uow.content.flush()
        uow.profiles.append(
            ProfileSnapshot(id=PROFILE_ID, revision=0, narrative="Deterministic systems"),
            expected_revision=None,
        )
        uow.assessments.add(
            CandidateAssessment(
                id=HIGH_ASSESSMENT_ID,
                content_id=high.id,
                profile_revision=0,
                relevance=0.9,
                quality=0.9,
                novelty=0.9,
                risk=0,
            )
        )
        uow.assessments.add(
            CandidateAssessment(
                id=LOW_ASSESSMENT_ID,
                content_id=low.id,
                profile_revision=0,
                relevance=0.8,
                quality=0.8,
                novelty=0.8,
                risk=0,
            )
        )
        uow.feed.add(FeedEntry(content_id=high.id, assessment_id=HIGH_ASSESSMENT_ID, position=0))
        uow.feed.add(FeedEntry(content_id=low.id, assessment_id=LOW_ASSESSMENT_ID, position=1))
        uow.commit()
    engine.dispose()


def test_feedback_changes_subsequent_api_ranking_without_mutating_feed_rows(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_url = f"sqlite:///{tmp_path / 'ranking.db'}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    _seed_ranked_feed(database_url)

    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", database_url)  # type: ignore[attr-defined]
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "e2e-ranking-token")  # type: ignore[attr-defined]
    monkeypatch.delenv("OPENBILICLAW_LITELLM_API_KEY", raising=False)  # type: ignore[attr-defined]
    headers = {"Authorization": "Bearer e2e-ranking-token"}

    with TestClient(create_app()) as client:
        before = client.get("/api/v1/feed", headers=headers)
        assert before.status_code == 200
        assert [item["content"]["id"] for item in before.json()] == [str(HIGH_ID), str(LOW_ID)]

        feedback = client.post(
            "/api/v1/interactions",
            headers=headers,
            json={"content_id": str(HIGH_ID), "kind": "negative"},
        )
        assert feedback.status_code == 201

        after = client.get("/api/v1/feed", headers=headers)
        assert after.status_code == 200
        assert [item["content"]["id"] for item in after.json()] == [str(LOW_ID), str(HIGH_ID)]
        page = client.get("/api/v1/feed?limit=1&offset=1", headers=headers)
        assert page.status_code == 200
        assert page.json()[0]["content"]["id"] == str(HIGH_ID)

    engine, session_factory = create_engine_and_session(DatabaseSettings(url=database_url))
    with UnitOfWork(session_factory) as uow:
        entries = uow.feed.list_entries(limit=10, offset=0)
        assert {item.entry.position for item in entries} == {0, 1}
        assert {item.entry.assessment_id for item in entries} == {
            HIGH_ASSESSMENT_ID,
            LOW_ASSESSMENT_ID,
        }
    engine.dispose()


def test_read_ranking_clamps_assessment_score_before_stable_pagination(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'clamped-ranking.db'}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=database_url))
    clamped = ContentItem(
        id=CLAMPED_ID,
        source_id="zhihu",
        external_id="clamped",
        url="https://www.zhihu.com/question/clamped",
        title="Domain score clamps to zero",
    )
    zero = ContentItem(
        id=ZERO_ID,
        source_id="zhihu",
        external_id="zero",
        url="https://www.zhihu.com/question/zero",
        title="Domain score is exactly zero",
    )
    with UnitOfWork(session_factory) as uow:
        uow.content.add(clamped)
        uow.content.add(zero)
        uow.content.flush()
        uow.profiles.append(
            ProfileSnapshot(revision=0, narrative="Assessment score clamp"),
            expected_revision=None,
        )
        uow.assessments.add(
            CandidateAssessment(
                id=CLAMPED_ASSESSMENT_ID,
                content_id=clamped.id,
                profile_revision=0,
                relevance=0,
                quality=0,
                novelty=0,
                risk=1,
            )
        )
        uow.assessments.add(
            CandidateAssessment(
                id=ZERO_ASSESSMENT_ID,
                content_id=zero.id,
                profile_revision=0,
                relevance=0,
                quality=0,
                novelty=0,
                risk=0,
            )
        )
        uow.feed.add(
            FeedEntry(content_id=clamped.id, assessment_id=CLAMPED_ASSESSMENT_ID, position=0)
        )
        uow.feed.add(FeedEntry(content_id=zero.id, assessment_id=ZERO_ASSESSMENT_ID, position=1))
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        first_page = uow.feed.list_entries(limit=1, offset=0)
        second_page = uow.feed.list_entries(limit=1, offset=1)
        all_entries = uow.feed.list_entries(limit=10, offset=0)

    assert [item.content.id for item in first_page] == [CLAMPED_ID]
    assert [item.content.id for item in second_page] == [ZERO_ID]
    assert [item.content.id for item in all_entries] == [CLAMPED_ID, ZERO_ID]
    assert [item.entry.position for item in all_entries] == [0, 1]
    assert [item.entry.assessment_id for item in all_entries] == [
        CLAMPED_ASSESSMENT_ID,
        ZERO_ASSESSMENT_ID,
    ]
    engine.dispose()
