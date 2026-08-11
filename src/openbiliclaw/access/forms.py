"""Declarative, provider-owned manual connection forms."""

from __future__ import annotations

import re
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated

from pydantic import ConfigDict, Field, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

from .models import InteractionKind, MethodId, ProviderId

if TYPE_CHECKING:
    from collections.abc import Mapping


class FieldKind(StrEnum):
    TEXT = "text"
    TOKEN = "token"
    COOKIE = "cookie"


class FormField(StrictBaseModel):
    """A safe field descriptor, never a submitted value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    label: str = Field(min_length=1, max_length=80)
    kind: FieldKind
    secret: bool
    required: bool = True
    min_length: int = Field(default=1, ge=0, le=65_536)
    max_length: int = Field(default=65_536, ge=1, le=65_536)
    pattern: str | None = None

    @model_validator(mode="after")
    def _bounds(self) -> FormField:
        if self.min_length > self.max_length:
            raise ValueError("min_length cannot exceed max_length")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("invalid field pattern") from exc
        if self.kind in {FieldKind.TOKEN, FieldKind.COOKIE} and not self.secret:
            raise ValueError("token and cookie fields must be secret")
        return self


class FormValidationError(ValueError):
    """Value-free validation error safe for logs and transports."""

    def __init__(self, field_id: str, code: str) -> None:
        super().__init__(f"invalid connection form field {field_id}: {code}")
        self.field_id = field_id
        self.code = code


class ValidatedSubmission:
    """Ephemeral values whose repr redacts secret fields.

    This is intentionally not a Pydantic model and has no serialization API.
    """

    __slots__ = ("_fields", "_values")

    def __init__(self, fields: tuple[FormField, ...], values: Mapping[str, str]) -> None:
        self._fields = fields
        self._values = MappingProxyType(dict(values))

    def value(self, field_id: str) -> str:
        return self._values[field_id]

    def items(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._values.items())

    def __repr__(self) -> str:
        descriptors = {field.field_id: field for field in self._fields}
        rendered = ", ".join(
            f"{key}=<redacted>" if descriptors[key].secret else f"{key}={value!r}"
            for key, value in self._values.items()
        )
        return f"ValidatedSubmission({rendered})"


class ConnectionForm(StrictBaseModel):
    """Declarative form contributed by a provider auth adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    method_id: MethodId
    interaction: InteractionKind
    fields: tuple[FormField, ...]

    @model_validator(mode="after")
    def _unique_fields(self) -> ConnectionForm:
        ids = [field.field_id for field in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate form field ID")
        if self.interaction is not InteractionKind.SECRET_FORM:
            raise ValueError("manual connection forms require secret_form interaction")
        if not any(field.secret for field in self.fields):
            raise ValueError("connection form must contain a secret field")
        return self

    def validate_submission(self, values: Mapping[str, str]) -> ValidatedSubmission:
        fields = {field.field_id: field for field in self.fields}
        unknown = set(values) - set(fields)
        if unknown:
            raise FormValidationError(sorted(unknown)[0], "unknown")
        validated: dict[str, str] = {}
        for field in self.fields:
            value = values.get(field.field_id)
            if value is None:
                if field.required:
                    raise FormValidationError(field.field_id, "missing")
                continue
            if not field.min_length <= len(value) <= field.max_length:
                raise FormValidationError(field.field_id, "length")
            if field.pattern is not None and re.fullmatch(field.pattern, value) is None:
                raise FormValidationError(field.field_id, "shape")
            validated[field.field_id] = value
        return ValidatedSubmission(self.fields, validated)
