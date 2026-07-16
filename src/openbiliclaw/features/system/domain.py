"""Typed user-facing vNext system settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class UserSettings(BaseModel):
    """Settings owned by OpenBiliClaw and configurable through the future UI."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    onboarding_complete: bool = False
    feed_low_watermark: int = Field(default=20, ge=0, le=1000)
    feed_high_watermark: int = Field(default=50, ge=1, le=2000)
    source_sync_interval_minutes: int = Field(default=30, ge=1, le=10080)

    @model_validator(mode="after")
    def validate_feed_watermarks(self) -> UserSettings:
        """Ensure refill boundaries describe a usable interval."""

        if self.feed_low_watermark > self.feed_high_watermark:
            raise ValueError("feed low watermark cannot exceed high watermark")
        return self
