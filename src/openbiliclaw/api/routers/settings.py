"""Typed mutable product settings routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool, model_validator

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.system.domain import (
    GenerativeAlias,
    LogLevel,
    ProxyMode,
    SourceId,
    TaskName,
    UserSettings,
)

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_access)])


class _PatchGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, data: object) -> object:
        if isinstance(data, dict) and any(value is None for value in data.values()):
            raise ValueError("settings patch values cannot be null")
        return data


class SourceSettingsPatch(_PatchGroup):
    enabled: dict[SourceId, StrictBool] | None = None
    weights: dict[SourceId, Annotated[FiniteFloat, Field(ge=0, le=100)]] | None = None


class ScheduleSettingsPatch(_PatchGroup):
    source_sync_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    profile_projection_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    feed_replenishment_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    cleanup_interval_minutes: int | None = Field(default=None, ge=1, le=10080)


class FeedSettingsPatch(_PatchGroup):
    low_watermark: int | None = Field(default=None, ge=0, le=1000)
    high_watermark: int | None = Field(default=None, ge=1, le=2000)
    candidate_multiplier: int | None = Field(default=None, ge=1, le=20)
    max_batch_candidates: int | None = Field(default=None, ge=1, le=100)
    min_score: FiniteFloat | None = Field(default=None, ge=0, le=1)
    min_novelty: FiniteFloat | None = Field(default=None, ge=0, le=1)
    max_per_source: int | None = Field(default=None, ge=1, le=100)
    max_per_topic: int | None = Field(default=None, ge=1, le=100)


class ProfileSettingsPatch(_PatchGroup):
    minimum_evidence_confidence: FiniteFloat | None = Field(default=None, ge=0, le=1)


class TaskSettingsPatch(_PatchGroup):
    model_alias: GenerativeAlias | None = None
    semantic_retry_limit: int | None = Field(default=None, ge=0, le=10)
    timeout_seconds: FiniteFloat | None = Field(default=None, ge=1, le=600)
    request_limit: int | None = Field(default=None, ge=1, le=20)
    total_tokens_limit: int | None = Field(default=None, ge=1, le=1_000_000)


class NetworkSettingsPatch(_PatchGroup):
    mode: ProxyMode | None = None
    proxy_url: str | None = Field(default=None, max_length=2048)


class LoggingSettingsPatch(_PatchGroup):
    console_level: LogLevel | None = None
    file_level: LogLevel | None = None


class AccessControlSettingsPatch(_PatchGroup):
    trust_loopback: StrictBool | None = None
    session_ttl_hours: int | None = Field(default=None, ge=0, le=8760)
    extension_access_enabled: StrictBool | None = None
    extension_session_ttl_hours: int | None = Field(default=None, ge=1, le=168)


class JobSettingsPatch(_PatchGroup):
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class UserSettingsPatch(_PatchGroup):
    """Strict recursive partial update; deployment-owned fields are intentionally absent."""

    sources: SourceSettingsPatch | None = None
    schedules: ScheduleSettingsPatch | None = None
    feed: FeedSettingsPatch | None = None
    profile: ProfileSettingsPatch | None = None
    tasks: dict[TaskName, TaskSettingsPatch] | None = None
    network: NetworkSettingsPatch | None = None
    logging: LoggingSettingsPatch | None = None
    access_control: AccessControlSettingsPatch | None = None
    jobs: JobSettingsPatch | None = None


@router.get("", operation_id="v1_settings_get", response_model=UserSettings)
def get_settings(container: Container) -> UserSettings:
    return container.settings.get()


@router.patch("", operation_id="v1_settings_patch", response_model=UserSettings)
def patch_settings(patch: UserSettingsPatch, container: Container) -> UserSettings:
    values = patch.model_dump(exclude_unset=True, exclude_none=True)
    return container.settings.update(values)


__all__ = ["UserSettingsPatch", "get_settings", "patch_settings", "router"]
