from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from openbiliclaw.access.models import AccessRequest
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.plugin_access import SubmitAccessMaterialCommand
from openbiliclaw.application.sources import ConnectSourceCommand, DisconnectSourceCommand

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    AccessMaterialRequest,
    AccessRecipeResponse,
    ConnectSourceRequest,
    DisconnectSourceRequest,
    SourceFormResponse,
    SourceListResponse,
    SourceMutationResponse,
    SourceStatusEntry,
)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=SourceListResponse)
async def list_sources(
    account_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceListResponse:
    result = await dependencies.facade.list_sources(account_id, limit)
    return SourceListResponse(
        items=tuple(
            SourceStatusEntry(
                provider_id=status.provider_id,
                account_id=status.account_id,
                state=status.state.value,
                method_id=status.method_id,
                verification=status.verification,
                capabilities=dependencies.facade.provider_capabilities(status.provider_id),
            )
            for status in result.items
        )
    )


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


@router.get("/{provider_id}/access-recipe", response_model=AccessRecipeResponse)
async def access_recipe(
    provider_id: str,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> AccessRecipeResponse:
    if dependencies.plugin_access is None:
        raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "plugin access is unavailable")
    return AccessRecipeResponse(recipe=dependencies.plugin_access.access_recipe(provider_id))


@router.post("/{provider_id}/access-material", response_model=SourceMutationResponse)
async def submit_access_material(
    provider_id: str,
    body: AccessMaterialRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceMutationResponse:
    if dependencies.plugin_access is None:
        raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "plugin access is unavailable")
    status = await dependencies.plugin_access.submit_access_material(
        SubmitAccessMaterialCommand(provider_id=provider_id, artifacts=body.artifacts)
    )
    return SourceMutationResponse(status=status)


@router.post("/connect", response_model=SourceMutationResponse)
async def connect_source(
    body: ConnectSourceRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SourceMutationResponse:
    submission = (
        {key: value.get_secret_value() for key, value in body.submission.items()}
        if body.submission is not None
        else None
    )
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
