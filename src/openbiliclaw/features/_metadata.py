"""Recursively immutable, JSON-safe metadata values for domain contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, TypeAlias, cast

from pydantic import JsonValue, PlainSerializer, PlainValidator, TypeAdapter, WithJsonSchema

JsonScalar: TypeAlias = str | int | float | bool | None
FrozenJsonValue: TypeAlias = (
    JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)


def _freeze_json(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if isinstance(value, Mapping):
        source = cast("Mapping[object, object]", value)
        frozen: dict[str, FrozenJsonValue] = {}
        for key, item in source.items():
            if not isinstance(key, str):
                raise ValueError("metadata object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise ValueError(f"metadata contains a non-JSON value: {type(value).__name__}")


def freeze_metadata(value: object) -> Mapping[str, JsonValue]:
    """Validate a metadata object and freeze every nested container."""

    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("metadata must be a JSON object")
    return cast("Mapping[str, JsonValue]", frozen)


def _thaw_json(value: FrozenJsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def serialize_metadata(value: Mapping[str, JsonValue]) -> dict[str, object]:
    """Return a plain JSON object for Pydantic's Python and JSON serializers."""

    return {key: _thaw_json(cast("FrozenJsonValue", item)) for key, item in value.items()}


def empty_metadata() -> Mapping[str, JsonValue]:
    """Create an immutable empty metadata object."""

    return MappingProxyType({})


FrozenMetadata: TypeAlias = Annotated[
    Mapping[str, JsonValue],
    PlainValidator(freeze_metadata),
    PlainSerializer(serialize_metadata, return_type=dict[str, object]),
    WithJsonSchema(TypeAdapter(dict[str, JsonValue]).json_schema()),
]
