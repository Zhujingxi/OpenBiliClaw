"""Xiaohongshu (小红书) source adapter — extension-driven content discovery.

All content discovery and metadata extraction happens in the user's
browser via the Chrome extension (passive URL collection, background-tab
search tasks, creator subscription fetches). The extension sends note
metadata (title, author, cover, URL) directly to the backend API, which
stores it in the shared ``discovery_candidates`` pending-evaluation pool.

This adapter exists so the ``AdapterRegistry`` has a ``"xiaohongshu"``
entry. Its ``fetch()`` is a no-op: the real data path is
``POST /api/sources/xhs/observed-urls`` → ``discovery_candidates``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)


class XiaohongshuAdapter:
    """Adapter stub — xhs content enters the system via the extension API,
    not through the adapter's ``fetch()`` method.

    Registered so that ``AdapterRegistry.has("xiaohongshu")`` returns True
    and multi-source pipeline code doesn't need special-casing.
    """

    @property
    def source_type(self) -> str:
        return "xiaohongshu"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """No-op — xhs content is ingested via observed-urls into the candidate pool."""
        logger.debug(
            "XiaohongshuAdapter.fetch() called but xhs content enters "
            "via extension API, not adapter.fetch(). Returning empty.",
        )
        return []
