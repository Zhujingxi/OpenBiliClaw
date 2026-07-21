"""Synchronous SQLAlchemy persistence for the fresh vNext database."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openbiliclaw.infrastructure.database.base import (
    DatabaseSettings,
    create_engine_and_session,
)

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.database.uow import UnitOfWork

__all__ = ["DatabaseSettings", "UnitOfWork", "create_engine_and_session"]


def __getattr__(name: str) -> Any:
    """Load the UoW lazily so source adapters can import task models without a cycle."""

    if name == "UnitOfWork":
        from openbiliclaw.infrastructure.database.uow import UnitOfWork

        return UnitOfWork
    raise AttributeError(name)
