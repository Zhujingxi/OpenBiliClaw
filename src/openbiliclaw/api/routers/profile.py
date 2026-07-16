"""Current revisioned evidence-profile route."""

from fastapi import APIRouter, Depends, HTTPException

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.profile.domain import ProfileSnapshot

router = APIRouter(prefix="/profile", tags=["profile"], dependencies=[Depends(require_access)])


@router.get("", operation_id="v1_profile_get", response_model=ProfileSnapshot)
def current_profile(container: Container) -> ProfileSnapshot:
    snapshot = container.profile.current()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="profile not projected")
    return snapshot
