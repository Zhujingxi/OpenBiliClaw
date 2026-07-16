"""Durable generic browser source-task contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select

from openbiliclaw.features.sources.domain import SourceCapability
from openbiliclaw.features.sources.service import (
    CredentialShapedPayloadError,
    SourceTaskCompletionConflictError,
    SourceTaskRequest,
    SourceTaskService,
    StaleSourceTaskLeaseError,
)
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import SourceTaskModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork

from .test_connector_contract import FakeTransport, make_registry

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def task_context(tmp_path: Path) -> tuple[Any, Any, SourceTaskService]:
    path = tmp_path / "tasks.db"
    url = f"sqlite:///{path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=url))
    transports = {
        source_id: FakeTransport(source_id)
        for source_id in (
            "bilibili",
            "xiaohongshu",
            "douyin",
            "youtube",
            "twitter",
            "zhihu",
            "reddit",
        )
    }
    registry = make_registry(transports)
    service = SourceTaskService(lambda: UnitOfWork(session_factory), registry, lease_seconds=60)
    yield session_factory, engine, service
    engine.dispose()


def test_task_request_is_typed_by_canonical_source_and_operation() -> None:
    request = SourceTaskRequest(
        source_id="zhihu",
        operation=SourceCapability.SEARCH,
        payload={"query": "python", "limit": 5},
    )
    assert request.operation is SourceCapability.SEARCH

    with pytest.raises(ValidationError):
        SourceTaskRequest(source_id="x", operation=SourceCapability.SEARCH, payload={})
    with pytest.raises(ValidationError):
        SourceTaskRequest(source_id="zhihu", operation="native_save", payload={})


def test_claim_is_lease_safe_and_scoped_to_source(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu",
            operation=SourceCapability.SEARCH,
            payload={"query": "python", "limit": 5},
        )
    )
    service.enqueue(
        SourceTaskRequest(
            source_id="reddit",
            operation=SourceCapability.SEARCH,
            payload={"query": "python", "limit": 5},
        )
    )

    claimed = service.claim("zhihu")

    assert claimed is not None
    assert claimed.id == task_id
    assert claimed.source_id == "zhihu"
    assert claimed.operation is SourceCapability.SEARCH
    assert claimed.lease_token
    assert service.claim("zhihu") is None


def test_expired_lease_can_be_reclaimed_but_old_token_cannot_complete(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    session_factory, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="reddit",
            operation=SourceCapability.RELATED,
            payload={"seed_url": "https://www.reddit.com/r/python/comments/1/example/"},
        )
    )
    first = service.claim("reddit")
    assert first is not None
    with session_factory() as session, session.begin():
        row = session.get(SourceTaskModel, str(task_id))
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second = service.claim("reddit")

    assert second is not None
    assert second.lease_token != first.lease_token
    with pytest.raises(StaleSourceTaskLeaseError):
        service.complete(task_id, first.lease_token, {"items": []})


def test_completion_is_idempotent_for_the_same_result(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="xiaohongshu",
            operation=SourceCapability.SEARCH,
            payload={"query": "python", "limit": 5},
        )
    )
    claimed = service.claim("xiaohongshu")
    assert claimed is not None

    first = service.complete(task_id, claimed.lease_token, {"items": [{"note_id": "1"}]})
    second = service.complete(task_id, claimed.lease_token, {"items": [{"note_id": "1"}]})

    assert first.idempotent is False
    assert second.idempotent is True

    with pytest.raises(SourceTaskCompletionConflictError):
        service.complete(task_id, claimed.lease_token, {"items": [{"note_id": "different"}]})


@pytest.mark.parametrize(
    "payload",
    [
        {"cookie": "session=do-not-store"},
        {"headers": {"Authorization": "Bearer do-not-store"}},
        {"headers": {"csrf_token": "do-not-store"}},
        {"items": object()},
    ],
)
def test_malformed_or_credential_shaped_task_payloads_are_rejected(
    task_context: tuple[Any, Any, SourceTaskService], payload: dict[str, object]
) -> None:
    session_factory, _, service = task_context

    error = (
        (CredentialShapedPayloadError, ValidationError)
        if "items" in payload
        else CredentialShapedPayloadError
    )
    with pytest.raises(error):
        service.enqueue(
            SourceTaskRequest(
                source_id="youtube",
                operation=SourceCapability.SEARCH,
                payload=payload,  # type: ignore[arg-type]
            )
        )

    with session_factory() as session:
        assert session.scalar(select(SourceTaskModel)) is None


def test_credential_shaped_completion_is_rejected_without_persisting_secret(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    session_factory, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="douyin",
            operation=SourceCapability.RECOMMENDED,
            payload={"limit": 5},
        )
    )
    claimed = service.claim("douyin")
    assert claimed is not None

    with pytest.raises(CredentialShapedPayloadError) as caught:
        service.complete(task_id, claimed.lease_token, {"access_token": "do-not-store"})

    assert "do-not-store" not in str(caught.value)
    with session_factory() as session:
        row = session.get(SourceTaskModel, str(task_id))
        assert row is not None
        assert row.result_payload is None
