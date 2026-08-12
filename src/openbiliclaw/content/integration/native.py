"""Provider-native content envelope without payload flattening."""

from __future__ import annotations

from pydantic import ConfigDict, Field, SerializeAsAny, field_validator

from openbiliclaw.core._pydantic import StrictBaseModel

from .identity import ContentRef  # noqa: TC001  # Pydantic resolves field types at runtime.


class NativeContent(StrictBaseModel):
    """Validated provider-native record at the heterogeneous registry boundary.

    Providers must validate external JSON into their own Pydantic model before
    constructing this envelope. Untyped mappings are rejected.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    ref: ContentRef
    schema_version: int = Field(ge=1)
    payload: SerializeAsAny[StrictBaseModel]

    @field_validator("payload", mode="before")
    @classmethod
    def _typed_payload_only(cls, value: object) -> object:
        if not isinstance(value, StrictBaseModel):
            raise ValueError("native payload must be a validated Pydantic model")
        return value
