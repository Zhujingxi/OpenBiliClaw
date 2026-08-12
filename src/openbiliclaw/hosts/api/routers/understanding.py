from __future__ import annotations

from fastapi import APIRouter, Depends

from openbiliclaw.application.edit_profile import EditProfileResult

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import ProfileEditRequest, ProfileResponse

router = APIRouter(prefix="/profiles", tags=["understanding"])


@router.get("/{profile_id}", response_model=ProfileResponse)
async def profile(
    profile_id: str,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ProfileResponse:
    return ProfileResponse(profile=(await dependencies.facade.show_profile(profile_id)).profile)


@router.post("/edit", response_model=EditProfileResult)
async def edit_profile(
    body: ProfileEditRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> EditProfileResult:
    return await dependencies.facade.edit_profile(body.to_command())
