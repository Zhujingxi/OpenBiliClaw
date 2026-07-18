"""Adapter registry — resolves source_type to the appropriate SourceAdapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.sources.protocol import SourceAdapter, SourceRecipe

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Maps ``source_type`` strings to :class:`SourceAdapter` instances."""

    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        """Register an adapter under its ``source_type``."""
        key = adapter.source_type
        self._adapters[key] = adapter
        logger.info("Registered source adapter: %s", key)

    def resolve(self, recipe: SourceRecipe) -> SourceAdapter | None:
        """Return the adapter matching *recipe.source_type*, or ``None``."""
        return self._adapters.get(recipe.source_type)

    def has(self, source_type: str) -> bool:
        """Check whether an adapter is registered for *source_type*."""
        return source_type in self._adapters

    @property
    def source_types(self) -> list[str]:
        """List all registered source type keys."""
        return list(self._adapters)
