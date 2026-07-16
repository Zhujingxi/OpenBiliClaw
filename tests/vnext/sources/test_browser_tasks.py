"""Durable generic browser source-task contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select

from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.features.sources.service import (
    CancelledSourceTaskError,
    CredentialShapedPayloadError,
    SourceTaskCompletionConflictError,
    SourceTaskRequest,
    SourceTaskService,
    StaleSourceTaskLeaseError,
)
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import SourceTaskModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork

from .test_connector_contract import RecordingTransport, make_registry

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
        source_id: RecordingTransport(source_id)
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
        operation=SourceOperation.SEARCH,
        payload={"query": "python", "limit": 5},
    )
    assert request.operation is SourceOperation.SEARCH

    with pytest.raises(ValidationError):
        SourceTaskRequest(source_id="x", operation=SourceOperation.SEARCH, payload={})
    with pytest.raises(ValidationError):
        SourceTaskRequest(source_id="zhihu", operation="native_save", payload={})


def test_claim_is_lease_safe_and_scoped_to_source(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu",
            operation=SourceOperation.SEARCH,
            payload={"query": "python", "limit": 5},
        )
    )
    service.enqueue(
        SourceTaskRequest(
            source_id="xiaohongshu",
            operation=SourceOperation.SEARCH,
            payload={"query": "python", "limit": 5},
        )
    )

    claimed = service.claim("zhihu")

    assert claimed is not None
    assert claimed.id == task_id
    assert claimed.source_id == "zhihu"
    assert claimed.operation is SourceOperation.SEARCH
    assert claimed.lease_token
    assert service.claim("zhihu") is None


def test_expired_lease_can_be_reclaimed_but_old_token_cannot_complete(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    session_factory, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu",
            operation=SourceOperation.RELATED,
            payload={"seed_url": "https://www.reddit.com/r/python/comments/1/example/"},
        )
    )
    first = service.claim("zhihu")
    assert first is not None
    with session_factory() as session, session.begin():
        row = session.get(SourceTaskModel, str(task_id))
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second = service.claim("zhihu")

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
            operation=SourceOperation.SEARCH,
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
                source_id="zhihu",
                operation=SourceOperation.SEARCH,
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
            source_id="zhihu",
            operation=SourceOperation.FEED,
            payload={"limit": 5},
        )
    )
    claimed = service.claim("zhihu")
    assert claimed is not None

    with pytest.raises(CredentialShapedPayloadError) as caught:
        service.complete(task_id, claimed.lease_token, {"access_token": "do-not-store"})

    assert "do-not-store" not in str(caught.value)
    with session_factory() as session:
        row = session.get(SourceTaskModel, str(task_id))
        assert row is not None
        assert row.result_payload is None


@pytest.mark.parametrize(
    "field",
    [
        "cookies",
        "credentials",
        "proxy_authorization",
        "request_authorization",
        "refresh_tokens",
        "nestedApiKeys",
        "cookie_jar",
        "authorization_header",
        "cookie_jars",
    ],
)
def test_plural_and_qualified_credential_containers_are_rejected_before_persistence(
    task_context: tuple[Any, Any, SourceTaskService], field: str
) -> None:
    session_factory, _, service = task_context
    secret = "unique-secret-value"
    with pytest.raises(CredentialShapedPayloadError) as caught:
        service.enqueue(
            SourceTaskRequest(
                source_id="zhihu",
                operation=SourceOperation.SEARCH,
                payload={"outer": {field: [secret]}},
            )
        )
    assert secret not in str(caught.value)
    with session_factory() as session:
        assert session.scalar(select(SourceTaskModel)) is None


@pytest.mark.parametrize("field", ["token_count", "session_duration", "cookie_policy"])
def test_non_secret_analytics_fields_are_not_false_positives(
    task_context: tuple[Any, Any, SourceTaskService], field: str
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu",
            operation=SourceOperation.SEARCH,
            payload={field: 1},
        )
    )
    claim = service.claim("zhihu")
    assert claim is not None
    service.complete(task_id, claim.lease_token, {field: 1, "items": []})


@pytest.mark.parametrize("field", ["cookie_jar", "authorization_header", "access_tokens"])
def test_qualified_credential_completion_is_rejected(
    task_context: tuple[Any, Any, SourceTaskService], field: str
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(source_id="zhihu", operation=SourceOperation.FEED, payload={"limit": 1})
    )
    claim = service.claim("zhihu")
    assert claim is not None
    with pytest.raises(CredentialShapedPayloadError):
        service.complete(task_id, claim.lease_token, {"outer": {field: "secret"}})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_never_reach_request_or_result_persistence(
    task_context: tuple[Any, Any, SourceTaskService], value: float
) -> None:
    session_factory, _, service = task_context
    with pytest.raises(ValidationError):
        SourceTaskRequest(
            source_id="zhihu",
            operation=SourceOperation.SEARCH,
            payload={"nested": [{"score": value}]},
        )
    task_id = service.enqueue(
        SourceTaskRequest(source_id="zhihu", operation=SourceOperation.FEED, payload={"limit": 1})
    )
    claim = service.claim("zhihu")
    assert claim is not None
    with pytest.raises(ValueError, match="finite"):
        service.complete(task_id, claim.lease_token, {"items": [{"score": value}]})
    with session_factory() as session:
        row = session.get(SourceTaskModel, str(task_id))
        assert row is not None
        assert row.result_payload is None


def test_cancelled_task_is_terminal_unclaimable_and_uncompletable(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu", operation=SourceOperation.SEARCH, payload={"query": "python"}
        )
    )
    claim = service.claim("zhihu")
    assert claim is not None
    snapshot = service.cancel(task_id)
    assert snapshot.status.value == "cancelled"
    assert service.claim("zhihu") is None
    with pytest.raises(CancelledSourceTaskError):
        service.complete(task_id, claim.lease_token, {"items": []})


def test_two_separate_uows_claim_one_task_for_exactly_one_owner(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu", operation=SourceOperation.SEARCH, payload={"query": "python"}
        )
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: service.claim("zhihu"), range(2)))
    owned = [claim for claim in claims if claim is not None]
    assert len(owned) == 1
    assert owned[0].id == task_id


def test_parallel_identical_completions_are_one_write_plus_one_idempotent_retry(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu", operation=SourceOperation.SEARCH, payload={"query": "python"}
        )
    )
    claim = service.claim("zhihu")
    assert claim is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        completions = list(
            pool.map(
                lambda _: service.complete(task_id, claim.lease_token, {"items": []}), range(2)
            )
        )
    assert sorted(item.idempotent for item in completions) == [False, True]


def test_parallel_conflicting_completions_preserve_one_result(
    task_context: tuple[Any, Any, SourceTaskService],
) -> None:
    _, _, service = task_context
    task_id = service.enqueue(
        SourceTaskRequest(
            source_id="zhihu", operation=SourceOperation.SEARCH, payload={"query": "python"}
        )
    )
    claim = service.claim("zhihu")
    assert claim is not None

    def complete(value: str) -> object:
        try:
            return service.complete(task_id, claim.lease_token, {"items": [{"id": value}]})
        except SourceTaskCompletionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(complete, ("a", "b")))
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, SourceTaskCompletionConflictError) for item in outcomes) == 1
