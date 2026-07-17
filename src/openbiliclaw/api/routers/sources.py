"""Source manifests, safe statuses, and encrypted account configuration."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.sources.domain import (
    SourceAccountDisconnectResult,
    SourceAccountStatus,
    SourceCredentialInput,
    SourceId,
    SourceManifest,
)


class SourceConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_key: str = Field(min_length=1, max_length=200)
    credentials: SourceCredentialInput


router = APIRouter(prefix="/sources", tags=["sources"], dependencies=[Depends(require_access)])


@router.get(
    "",
    operation_id="v1_sources_list",
    response_model=tuple[SourceManifest, ...],
)
def list_sources(container: Container) -> tuple[SourceManifest, ...]:
    return container.sources.manifests()


@router.get(
    "/status",
    operation_id="v1_sources_status",
    response_model=tuple[SourceAccountStatus, ...],
)
def source_status(container: Container) -> tuple[SourceAccountStatus, ...]:
    return container.sources.statuses()


@router.put(
    "/{source_id}/accounts",
    operation_id="v1_sources_configure_account",
    response_model=SourceAccountStatus,
)
def configure_source(
    source_id: SourceId,
    payload: SourceConfiguration,
    container: Container,
) -> SourceAccountStatus:
    return container.sources.configure(
        source_id,
        payload.account_key,
        {"cookie": payload.credentials.cookie.get_secret_value()},
    )


@router.delete(
    "/{source_id}/accounts/{account_key}",
    operation_id="v1_sources_disconnect_account",
    response_model=SourceAccountDisconnectResult,
)
def disconnect_source(
    source_id: SourceId,
    account_key: str,
    container: Container,
) -> SourceAccountDisconnectResult:
    """Idempotently delete one source account's encrypted credential material."""

    return container.sources.disconnect(source_id, account_key)
