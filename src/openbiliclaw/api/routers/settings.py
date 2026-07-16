"""Typed mutable product settings routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.system.domain import UserSettings

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_access)])


class UserSettingsPatch(BaseModel):
    """Typed partial update; omitted fields retain their current value."""

    model_config = ConfigDict(extra="forbid", strict=True)

    onboarding_complete: bool | None = None
    feed_low_watermark: int | None = Field(default=None, ge=0, le=1000)
    feed_high_watermark: int | None = Field(default=None, ge=1, le=2000)
    source_sync_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    source_weights: dict[str, float] | None = None
    source_enabled: dict[str, bool] | None = None


@router.get("", operation_id="v1_settings_get", response_model=UserSettings)
def get_settings(container: Container) -> UserSettings:
    return container.settings.get()


@router.patch("", operation_id="v1_settings_patch", response_model=UserSettings)
def patch_settings(patch: UserSettingsPatch, container: Container) -> UserSettings:
    values = patch.model_dump(exclude_unset=True, exclude_none=True)
    return container.settings.update(values)
