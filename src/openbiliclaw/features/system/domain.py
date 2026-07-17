"""Typed user-facing vNext system settings."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

SourceId = Literal[
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
]
GenerativeAlias = Literal["obc-interactive", "obc-analysis"]
TaskName = Literal[
    "profile_delta",
    "keyword_generation",
    "candidate_assessment",
    "candidate_batch_assessment",
    "chat_response",
    "recommendation_explanation",
]
ProxyMode = Literal["direct", "system", "custom"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_SOURCE_IDS: tuple[SourceId, ...] = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
)
_TASK_NAMES: tuple[TaskName, ...] = (
    "profile_delta",
    "keyword_generation",
    "candidate_assessment",
    "candidate_batch_assessment",
    "chat_response",
    "recommendation_explanation",
)
_READ_ONLY: dict[str, Any] = {"readOnly": True, "x-mutability": "deployment"}


def _default_source_weights() -> dict[SourceId, float]:
    return dict.fromkeys(_SOURCE_IDS, 1.0)


def _default_source_enabled() -> dict[SourceId, bool]:
    return dict.fromkeys(_SOURCE_IDS, False)


DEFAULT_DATABASE_URL = "sqlite:///data/vnext/openbiliclaw.db"
DEFAULT_DATABASE_BUSY_TIMEOUT_SECONDS = 5.0


class DatabaseSettings(BaseSettings):
    """Environment-backed location and diagnostics for the isolated vNext database."""

    model_config = SettingsConfigDict(
        env_prefix="OPENBILICLAW_DATABASE_",
        extra="ignore",
        frozen=True,
    )

    url: str = Field(default=DEFAULT_DATABASE_URL, min_length=1)
    echo: bool = False
    busy_timeout_seconds: float = Field(
        default=DEFAULT_DATABASE_BUSY_TIMEOUT_SECONDS, ge=0.001, le=60
    )


class _SettingsGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SourceSettings(_SettingsGroup):
    """Built-in source admission and relative collection allocation."""

    enabled: dict[SourceId, StrictBool] = Field(default_factory=_default_source_enabled)
    weights: dict[SourceId, Annotated[FiniteFloat, Field(ge=0, le=100)]] = Field(
        default_factory=_default_source_weights
    )

    @model_validator(mode="after")
    def require_complete_source_maps(self) -> SourceSettings:
        expected = set(_SOURCE_IDS)
        if set(self.enabled) != expected or set(self.weights) != expected:
            raise ValueError("source settings must contain every built-in source ID")
        return self


class ScheduleSettings(_SettingsGroup):
    """User-controlled durable job schedules."""

    source_sync_interval_minutes: int = Field(default=30, ge=1, le=10080)


class FeedSettings(_SettingsGroup):
    """Bounded collection and admission policy used by FeedService."""

    low_watermark: int = Field(default=20, ge=0, le=1000)
    high_watermark: int = Field(default=50, ge=1, le=2000)
    candidate_multiplier: int = Field(default=3, ge=1, le=20)
    max_batch_candidates: int = Field(default=100, ge=1, le=100)
    min_score: FiniteFloat = Field(default=0.55, ge=0, le=1)
    min_novelty: FiniteFloat = Field(default=0.2, ge=0, le=1)
    max_per_source: int = Field(default=4, ge=1, le=100)
    max_per_topic: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def validate_watermarks(self) -> FeedSettings:
        if self.low_watermark > self.high_watermark:
            raise ValueError("feed low watermark cannot exceed high watermark")
        return self


class ProfileSettings(_SettingsGroup):
    """Evidence admission threshold for scheduled profile projection."""

    minimum_evidence_confidence: FiniteFloat = Field(default=0.0, ge=0, le=1)


class TaskSettings(_SettingsGroup):
    """Product-level semantic task alias and bounded execution limits."""

    model_alias: GenerativeAlias
    semantic_retry_limit: int = Field(ge=0, le=10)
    timeout_seconds: FiniteFloat = Field(ge=1, le=600)
    request_limit: int = Field(ge=1, le=20)
    total_tokens_limit: int = Field(ge=1, le=1_000_000)


def _default_tasks() -> dict[TaskName, TaskSettings]:
    return {
        "profile_delta": TaskSettings(
            model_alias="obc-analysis",
            semantic_retry_limit=2,
            timeout_seconds=90,
            request_limit=3,
            total_tokens_limit=12_000,
        ),
        "keyword_generation": TaskSettings(
            model_alias="obc-analysis",
            semantic_retry_limit=2,
            timeout_seconds=60,
            request_limit=3,
            total_tokens_limit=8_000,
        ),
        "candidate_assessment": TaskSettings(
            model_alias="obc-analysis",
            semantic_retry_limit=2,
            timeout_seconds=60,
            request_limit=3,
            total_tokens_limit=8_000,
        ),
        "candidate_batch_assessment": TaskSettings(
            model_alias="obc-analysis",
            semantic_retry_limit=2,
            timeout_seconds=90,
            request_limit=3,
            total_tokens_limit=24_000,
        ),
        "chat_response": TaskSettings(
            model_alias="obc-interactive",
            semantic_retry_limit=1,
            timeout_seconds=45,
            request_limit=2,
            total_tokens_limit=8_000,
        ),
        "recommendation_explanation": TaskSettings(
            model_alias="obc-interactive",
            semantic_retry_limit=1,
            timeout_seconds=30,
            request_limit=2,
            total_tokens_limit=4_000,
        ),
    }


class NetworkSettings(_SettingsGroup):
    """Secret-free outbound routing policy for supported overseas clients."""

    mode: ProxyMode = "direct"
    proxy_url: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def validate_proxy(self) -> NetworkSettings:
        if self.mode != "custom":
            if self.proxy_url:
                raise ValueError("proxy URL is only valid in custom network mode")
            return self
        parsed = urlsplit(self.proxy_url)
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
            raise ValueError("custom network mode requires an absolute supported proxy URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("proxy URL credentials are not accepted in product settings")
        if parsed.query or parsed.fragment:
            raise ValueError("proxy URL query and fragment are not accepted")
        return self


class LoggingSettings(_SettingsGroup):
    """Runtime log thresholds plus the deployment-owned output location."""

    console_level: LogLevel = "INFO"
    file_level: LogLevel = "DEBUG"
    directory: str = Field(default="logs", json_schema_extra=_READ_ONLY)


class AccessControlSettings(_SettingsGroup):
    """Mutable access behavior and secret-free deployment readiness flags."""

    web_password_enabled: StrictBool = False
    trust_loopback: StrictBool = False
    session_ttl_hours: int = Field(default=24, ge=0, le=8760)
    extension_access_enabled: StrictBool = True
    extension_session_ttl_hours: int = Field(default=24, ge=1, le=168)
    installer_bearer_configured: bool = Field(default=False, json_schema_extra=_READ_ONLY)
    password_configured: bool = Field(default=False, json_schema_extra=_READ_ONLY)


class JobSettings(_SettingsGroup):
    """Mutable cleanup retention and deployment-owned worker concurrency."""

    retention_days: int = Field(default=30, ge=1, le=3650)
    worker_concurrency: int = Field(default=4, ge=1, le=4, json_schema_extra=_READ_ONLY)


class UserSettings(BaseModel):
    """Complete secret-free product settings persisted in the application database."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    onboarding_complete: bool = Field(default=False, json_schema_extra={"readOnly": True})
    sources: SourceSettings = Field(default_factory=SourceSettings)
    schedules: ScheduleSettings = Field(default_factory=ScheduleSettings)
    feed: FeedSettings = Field(default_factory=FeedSettings)
    profile: ProfileSettings = Field(default_factory=ProfileSettings)
    tasks: dict[TaskName, TaskSettings] = Field(default_factory=_default_tasks)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    access_control: AccessControlSettings = Field(default_factory=AccessControlSettings)
    jobs: JobSettings = Field(default_factory=JobSettings)

    @model_validator(mode="before")
    @classmethod
    def fill_partial_task_map(cls, data: object) -> object:
        if isinstance(data, Mapping):
            translated = dict(data)
            task_patch = translated.get("tasks")
            if isinstance(task_patch, Mapping):
                defaults = {name: task.model_dump() for name, task in _default_tasks().items()}
                for name, value in task_patch.items():
                    if isinstance(value, Mapping) and name in defaults:
                        defaults[name] = {**defaults[name], **value}
                    else:
                        defaults[name] = value
                translated["tasks"] = defaults
            return translated
        return data

    @model_validator(mode="after")
    def require_all_tasks(self) -> UserSettings:
        if set(self.tasks) != set(_TASK_NAMES):
            raise ValueError("task settings must contain every built-in task")
        if any(not math.isfinite(task.timeout_seconds) for task in self.tasks.values()):
            raise ValueError("task timeout must be finite")
        return self


__all__ = [
    "AccessControlSettings",
    "DatabaseSettings",
    "FeedSettings",
    "GenerativeAlias",
    "JobSettings",
    "LogLevel",
    "LoggingSettings",
    "NetworkSettings",
    "ProxyMode",
    "ProfileSettings",
    "ScheduleSettings",
    "SourceSettings",
    "SourceId",
    "TaskName",
    "TaskSettings",
    "UserSettings",
]
