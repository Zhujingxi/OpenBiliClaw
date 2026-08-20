"""Shared restored-or-anonymous public access setup for sequential real-stack layers."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from openbiliclaw.access.models import AccessRequest, AccessStatusKind, Permission
from openbiliclaw.application.sources import ConnectSourceCommand

if TYPE_CHECKING:
    from openbiliclaw.composition.facade import CompositionFacade


async def ensure_bilibili_public_access(facade: CompositionFacade) -> None:
    """Reuse verified restored access, connecting anonymously only when disconnected."""

    status = (await facade.source_status("bilibili", None)).status
    if status.state is AccessStatusKind.DISCONNECTED:
        status = (
            await facade.connect_source(
                ConnectSourceCommand(
                    idempotency_key=f"e2e:public:anonymous:{uuid.uuid4().hex}",
                    request=AccessRequest(
                        provider_id="bilibili",
                        permissions=frozenset({Permission.READ_PUBLIC}),
                        supported_method_ids=("builtin.anonymous",),
                    ),
                    allowed_method_ids=frozenset({"builtin.anonymous"}),
                )
            )
        ).status
    verification = status.verification
    if (
        status.state is not AccessStatusKind.CONNECTED
        or verification is None
        or Permission.READ_PUBLIC not in verification.granted_permissions
    ):
        raise AssertionError(
            "E2E requires connected Bilibili access granting read_public; "
            f"got state={status.state.value}"
        )
