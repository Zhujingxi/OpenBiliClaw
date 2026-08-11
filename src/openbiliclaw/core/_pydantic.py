"""Typed view of Pydantic's dynamic BaseModel boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    _Model = TypeVar("_Model", bound="StrictBaseModel")

    class StrictBaseModel:
        """Methods used by Core without inheriting Pydantic's metaclass Any."""

        def __init__(self, **data: object) -> None: ...

        @classmethod
        def model_validate(cls: type[_Model], value: object) -> _Model: ...

        def model_copy(
            self: _Model,
            *,
            update: Mapping[str, object] | None = None,
            deep: bool = False,
        ) -> _Model: ...

        @classmethod
        def model_validate_json(cls: type[_Model], value: str | bytes) -> _Model: ...

        def model_dump_json(self) -> str: ...
else:
    from pydantic import BaseModel as StrictBaseModel

__all__ = ["StrictBaseModel"]
