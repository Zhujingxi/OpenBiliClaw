from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from openbiliclaw.access.models import AccessRequest
from openbiliclaw.application.sources import ConnectSourceCommand, DisconnectSourceCommand

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    ConnectSourceRequest,
    DisconnectSourceRequest,
    SourceFormResponse,
    SourceListResponse,
    SourceMutationResponse,
)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=SourceListResponse)
async def list_sources(
    account_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceListResponse:
    result = await dependencies.facade.list_sources(account_id, limit)
    return SourceListResponse(items=result.items)


@router.get("/{provider_id}/status", response_model=SourceMutationResponse)
async def source_status(
    provider_id: str,
    account_id: str | None = None,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceMutationResponse:
    result = await dependencies.facade.source_status(provider_id, account_id)
    return SourceMutationResponse(status=result.status)


@router.get("/{provider_id}/forms/{method_id}", response_model=SourceFormResponse)
async def source_form(
    provider_id: str,
    method_id: str,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceFormResponse:
    return SourceFormResponse(form=await dependencies.facade.source_form(provider_id, method_id))


@router.post("/connect", response_model=SourceMutationResponse)
async def connect_source(
    body: ConnectSourceRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceMutationResponse:
    secret = body.credential.get_secret_value() if body.credential is not None else None
    submission = {"credential": secret} if secret is not None else None
    result = await dependencies.facade.connect_source(
        ConnectSourceCommand(
            idempotency_key=body.idempotency_key,
            request=AccessRequest(
                provider_id=body.provider_id,
                account_id=body.account_id,
                permissions=body.permissions,
                supported_method_ids=(body.method_id,),
            ),
            allowed_method_ids=frozenset({body.method_id}),
            submission=submission,
        )
    )
    return SourceMutationResponse(
        status=result.status,
        availability_refreshed=result.availability_refreshed,
        recoverable=result.recoverable,
    )


@router.post("/disconnect", response_model=SourceMutationResponse)
async def disconnect_source(
    body: DisconnectSourceRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceMutationResponse:
    status = await dependencies.facade.disconnect_source(
        DisconnectSourceCommand(
            idempotency_key=body.idempotency_key,
            provider_id=body.provider_id,
            account_id=body.account_id,
        )
    )
    return SourceMutationResponse(status=status)
