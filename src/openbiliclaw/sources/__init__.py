"""Multi-source content discovery adapters.

This package provides the SourceAdapter protocol and concrete adapters
that fetch content from various platforms (Bilibili, Xiaohongshu, web, etc.)
and normalise it into DiscoveredContent for the evaluation/recommendation
pipeline.
"""

from openbiliclaw.sources.protocol import SourceAdapter, SourceRecipe
from openbiliclaw.sources.registry import AdapterRegistry

__all__ = [
    "AdapterRegistry",
    "SourceAdapter",
    "SourceRecipe",
]
