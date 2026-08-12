"""Typed provider mutation requests and results."""

from __future__ import annotations

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

from .identity import (
    ContentRef,  # noqa: TC001  # Pydantic resolves field types at runtime.  # Runtime type required by Pydantic model fields.
)


class ActionConfirmation(StrictBaseModel):
    """Human-readable confirmation metadata owned by the application flow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=500)
    expires_at: AwareDatetime


class ActionRequest(StrictBaseModel):
    """Base request for an explicitly confirmed idempotent mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef
    idempotency_key: str = Field(min_length=8, max_length=200)
    confirmation: ActionConfirmation


class ActionResult(StrictBaseModel):
    """Safe result of a completed provider mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef
    idempotency_key: str = Field(min_length=8, max_length=200)
    completed_at: AwareDatetime
