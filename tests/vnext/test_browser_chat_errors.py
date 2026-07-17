"""Focused browser contracts for persisted chat reads and typed HTTP errors."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.dependencies import (
    AccessPolicy,
    ApplicationContainer,
    DependencyUnavailableError,
)
from openbiliclaw.features.chat.domain import ChatRole, ChatTurn
from openbiliclaw.features.chat.service import ChatHistoryPage
from openbiliclaw.features.system.domain import DatabaseSettings, UserSettings
from openbiliclaw.infrastructure.database.base import Base, create_engine_and_session
from openbiliclaw.infrastructure.database.repositories import ProfileRevisionConflict
from openbiliclaw.infrastructure.database.uow import UnitOfWork

TOKEN = "test-only-access-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONVERSATION = UUID("00000000-0000-0000-0000-000000000101")
OTHER_CONVERSATION = UUID("00000000-0000-0000-0000-000000000102")
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


class _Settings:
    def get(self) -> UserSettings:
        return UserSettings()


class _Profile:
    def current(self) -> None:
        return None


class _Chat:
    def __init__(self, page: ChatHistoryPage | None = None) -> None:
        self.page = page

    def history(self, *, conversation_id: UUID, limit: int, offset: int) -> ChatHistoryPage:
        assert self.page is not None
        assert conversation_id == self.page.conversation_id
        return self.page.model_copy(update={"limit": limit, "offset": offset})


def _container(*, chat: object | None = None) -> ApplicationContainer:
    unavailable = cast("Any", SimpleNamespace())
    return ApplicationContainer(
        access=AccessPolicy(token=TOKEN),
        settings=_Settings(),
        onboarding=unavailable,
        sources=unavailable,
        source_tasks=unavailable,
        activity=unavailable,
        profile=_Profile(),
        feed=unavailable,
        feedback=unavailable,
        library=unavailable,
        chat=cast("Any", chat or _Chat()),
        jobs=unavailable,
        ai_health=unavailable,
    )


def _turn(
    turn_id: int,
    *,
    conversation_id: UUID = CONVERSATION,
    role: ChatRole = ChatRole.USER,
    content: str | None = None,
) -> ChatTurn:
    return ChatTurn(
        id=UUID(int=turn_id),
        conversation_id=conversation_id,
        role=role,
        content=content or f"turn-{turn_id}",
        created_at=NOW,
    )


def test_chat_repository_history_is_isolated_ordered_and_paginated() -> None:
    engine, session_factory = create_engine_and_session(DatabaseSettings(url="sqlite://"))
    Base.metadata.create_all(engine)
    with UnitOfWork(session_factory) as uow:
        for turn in (_turn(3), _turn(1), _turn(2), _turn(4, conversation_id=OTHER_CONVERSATION)):
            uow.chat.add(turn)
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        first = uow.chat.list_by_conversation(CONVERSATION, limit=2, offset=0)
        second = uow.chat.list_by_conversation(CONVERSATION, limit=2, offset=2)

    assert [turn.id for turn in first] == [UUID(int=1), UUID(int=2)]
    assert [turn.id for turn in second] == [UUID(int=3)]
    assert all(turn.conversation_id == CONVERSATION for turn in (*first, *second))
    engine.dispose()


def test_chat_history_api_is_typed_bounded_and_hides_ai_run_internals() -> None:
    secret_internal_id = UUID("00000000-0000-0000-0000-00000000dead")
    persisted = _turn(1, role=ChatRole.ASSISTANT).model_copy(
        update={"ai_run_id": secret_internal_id}
    )
    page = ChatHistoryPage.from_turns(
        conversation_id=CONVERSATION,
        turns=(persisted,),
        limit=20,
        offset=0,
        has_more=False,
    )
    client = TestClient(create_app(container=_container(chat=_Chat(page))))

    response = client.get(f"/api/v1/chat/{CONVERSATION}?limit=20&offset=0", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": str(CONVERSATION),
        "items": [
            {
                "id": str(persisted.id),
                "role": "assistant",
                "content": persisted.content,
                "created_at": "2026-07-17T12:00:00Z",
            }
        ],
        "limit": 20,
        "offset": 0,
        "has_more": False,
    }
    assert str(secret_internal_id) not in response.text
    assert "ai_run" not in response.text
    assert client.get(f"/api/v1/chat/{CONVERSATION}?limit=101", headers=AUTH).status_code == 422
    assert client.get(f"/api/v1/chat/{CONVERSATION}?offset=-1", headers=AUTH).status_code == 422
    assert client.get(f"/api/v1/chat/{CONVERSATION}").status_code == 401


@pytest.mark.parametrize(
    ("case", "expected_status", "expected_code"),
    (
        ("unauthorized", 401, "unauthorized"),
        ("forbidden", 403, "forbidden"),
        ("not_found", 404, "not_found"),
        ("conflict", 409, "conflict"),
        ("validation", 422, "validation_error"),
        ("unavailable", 503, "unavailable"),
        ("internal", 500, "internal_error"),
    ),
)
def test_runtime_errors_share_one_safe_typed_envelope(
    case: str, expected_status: int, expected_code: str
) -> None:
    container = _container()
    headers = AUTH
    path = "/api/v1/settings"
    if case == "unauthorized":
        headers = {}
    elif case == "forbidden":
        headers = {"Authorization": "Bearer wrong-token"}
    elif case == "not_found":
        path = "/api/v1/profile"
    elif case == "conflict":
        container.settings.get = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            ProfileRevisionConflict("SQL users password=secret")
        )
    elif case == "validation":
        path = "/api/v1/settings?unexpected=query"
        container.settings.get = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            ValueError("provider api_key=secret")
        )
    elif case == "unavailable":
        container.settings.get = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            DependencyUnavailableError("upstream bearer secret")
        )
    elif case == "internal":
        container.settings.get = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("traceback SELECT * FROM credentials password=secret")
        )

    response = TestClient(create_app(container=container), raise_server_exceptions=False).get(
        path, headers=headers
    )

    assert response.status_code == expected_status
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": response.json()["error"]["message"],
        }
    }
    assert set(response.json()["error"]) == {"code", "message"}
    payload = response.text.casefold()
    for secret_fragment in ("traceback", "select *", "password", "api_key", "bearer secret"):
        assert secret_fragment not in payload
    if expected_status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_openapi_uses_error_envelope_without_losing_security_or_sse_metadata() -> None:
    schema = create_app().openapi()
    chat_stream = schema["paths"]["/api/v1/chat/stream"]["post"]
    chat_history = schema["paths"]["/api/v1/chat/{conversation_id}"]["get"]

    for operation in (chat_stream, chat_history):
        for status_code in ("401", "403", "404", "409", "422", "429", "500", "503"):
            assert operation["responses"][status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorEnvelope"
            }
        assert operation["security"] == [{"BearerAuth": []}, {"SessionCookie": []}]

    success = chat_stream["responses"]["200"]["content"]["text/event-stream"]
    assert set(success["x-sse-events"]) == {"delta", "done", "error"}
    assert schema["components"]["schemas"]["ErrorEnvelope"]["required"] == ["error"]
