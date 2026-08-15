from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi import HTTPException, Request, WebSocket

from openbiliclaw.hosts.api.dependencies import HostDependencies
from openbiliclaw.hosts.api.errors import http_error_handler, unexpected_error_handler
from openbiliclaw.hosts.api.routers.content import confirm, propose
from openbiliclaw.hosts.api.routers.events import _websocket_authorized, websocket
from openbiliclaw.hosts.api.routers.feedback import feedback
from openbiliclaw.hosts.api.schemas.models import (
    ConfirmActionRequest,
    FeedbackRequest,
    ObservationsRequest,
    ProposeActionRequest,
)
from openbiliclaw.recommendation.models import FeedbackKind

from .test_api import NOW, REF, Facade


class RequestStub:
    pass


@dataclass(slots=True)
class WebSocketStub:
    dependencies: HostDependencies
    headers: dict[str, str]
    accepted: bool = False
    closed: int | None = None
    sent: int = 0

    @property
    def app(self) -> object:
        return object()

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int, reason: str = "") -> None:
        del reason
        self.closed = code

    async def send_bytes(self, value: bytes) -> None:
        assert value
        self.sent += 1


async def test_error_helper_edges() -> None:
    request = RequestStub()
    for status in (404, 405, 418):
        response = await http_error_handler(
            cast("Request", request), HTTPException(status_code=status)
        )
        assert response.status_code == status
    response = await unexpected_error_handler(cast("Request", request), RuntimeError("secret"))
    assert response.status_code == 500


async def test_feedback_and_action_success_units() -> None:
    facade = Facade()
    deps = HostDependencies(facade=facade)
    feedback_result = await feedback(
        FeedbackRequest(
            idempotency_key="feedback:1",
            shown_id="shown_1",
            content_ref=REF,
            kind=FeedbackKind.LIKED,
        ),
        deps,
    )
    assert feedback_result.result.inserted
    proposed = await propose(
        ProposeActionRequest(
            idempotency_key="action:1",
            action_id="save",
            ref=REF,
            user_id="u",
            safe_preview="save",
        ),
        deps,
    )
    confirmed = await confirm(
        ConfirmActionRequest(pending_action_id=proposed.action.pending_action_id, user_id="u"),
        deps,
    )
    assert confirmed.result.action_id == "save"


async def test_websocket_handshake_auth_origin_and_admission() -> None:
    policy = HostDependencies(facade=Facade()).security.model_copy(update={"bearer_token": "key"})
    deps = HostDependencies(facade=Facade(), security=policy)
    assert not await _websocket_authorized(cast("WebSocket", WebSocketStub(deps, {})), deps)
    assert not await _websocket_authorized(
        cast("WebSocket", WebSocketStub(deps, {"authorization": "Bearer wrong"})), deps
    )
    assert not await _websocket_authorized(
        cast(
            "WebSocket",
            WebSocketStub(deps, {"authorization": "Bearer key", "origin": "https://evil"}),
        ),
        deps,
    )
    assert await _websocket_authorized(
        cast(
            "WebSocket",
            WebSocketStub(
                deps,
                {"authorization": "Bearer key", "origin": "http://localhost:8420"},
            ),
        ),
        deps,
    )


async def test_websocket_rejected_and_success_paths() -> None:
    deps = HostDependencies(facade=Facade(), events=None)
    rejected_policy = deps.security.model_copy(update={"bearer_token": "key"})
    rejected_deps = HostDependencies(facade=Facade(), security=rejected_policy)
    rejected = WebSocketStub(rejected_deps, {})
    await websocket(cast("WebSocket", rejected), 0, rejected_deps)
    assert rejected.closed == 1008

    accepted = WebSocketStub(deps, {})
    await websocket(cast("WebSocket", accepted), 0, deps)
    assert accepted.accepted and accepted.closed == 1000

    for _ in range(deps.security.max_websocket_subscribers):
        await deps.websocket_slots.acquire()
    limited = WebSocketStub(deps, {})
    await websocket(cast("WebSocket", limited), 0, deps)
    assert limited.closed == 1013
    for _ in range(deps.security.max_websocket_subscribers):
        deps.websocket_slots.release()


async def test_observations_success_unit() -> None:
    from openbiliclaw.hosts.api.routers.feedback import observations
    from openbiliclaw.observations.models import (
        ContentOpenedObservation,
        HostOpenPayload,
    )
    from openbiliclaw.observations.provenance import (
        ObservationProvenance,
        ObservationSource,
        TrustLevel,
    )

    facade = Facade()
    event = ContentOpenedObservation(
        observation_id="obs_" + "1" * 32,
        idempotency_key="event:0001",
        occurred_at=NOW,
        received_at=NOW,
        content_ref=REF,
        provenance=ObservationProvenance(
            producer_id="host.web",
            source=ObservationSource.HOST,
            authenticated=False,
            trust_level=TrustLevel.LOW,
        ),
        payload=HostOpenPayload(surface="web"),
    )
    result = await observations(
        ObservationsRequest(
            idempotency_key="batch:001",
            observations=[event],
            allowed_event_types=[event.event_type],
        ),
        HostDependencies(facade=facade),
    )
    assert result.result.items == ()
