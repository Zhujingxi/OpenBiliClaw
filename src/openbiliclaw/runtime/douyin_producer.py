"""Runtime Douyin discovery producer.

The continuous refresh controller owns pool quotas. This producer owns
the throttled call into the reusable Douyin discovery service when the
Douyin platform family is under quota.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult

logger = logging.getLogger(__name__)

DouyinDiscoverCallable = Callable[[Any, DouyinDiscoveryOptions], Awaitable[DouyinDiscoveryResult]]


def douyin_runtime_hot_budget(*, base_budget: int, requested_limit: int) -> int:
    """Return the effective hot-task budget for one runtime replenishment run."""
    configured = int(base_budget)
    if configured <= 0:
        return 0
    requested = max(1, int(requested_limit))
    if requested < 10:
        return configured
    return max(configured, min(60, requested))


@dataclass
class DouyinDiscoveryProducer:
    """Throttle and invoke Douyin discovery from the runtime loop."""

    soul_engine: Any
    discover: DouyinDiscoverCallable
    enabled: bool = True
    min_interval_minutes: int = 30
    sources: tuple[str, ...] = ("search", "hot", "feed")
    evaluate: bool = True
    per_source_limit: int = 20
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one Douyin discovery cycle if enabled and due."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("douyin producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or self.per_source_limit))
        selected_sources = self._sources_for_limit(requested_limit)
        per_source_limit = max(
            1,
            min(
                self.per_source_limit,
                math.ceil(requested_limit / max(1, len(selected_sources))),
            ),
        )
        options = DouyinDiscoveryOptions(
            limit=requested_limit,
            sources=selected_sources,
            cache=True,
            evaluate=self.evaluate,
            per_source_limit=per_source_limit,
            keywords_per_run=1,
        )
        try:
            result = await self.discover(profile, options)
        except Exception as exc:
            logger.warning("douyin producer failed: %s", exc)
            return self._skip("error")

        self._last_run_at = datetime.now(UTC)
        return {
            "discovered": len(result.items),
            "cached": result.cached,
            "source_counts": dict(result.source_counts),
            "reason": "ok",
        }

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _sources_for_limit(self, requested_limit: int) -> tuple[str, ...]:
        configured = tuple(source for source in self.sources if str(source).strip())
        if requested_limit >= 10:
            selected = tuple(source for source in ("search", "hot") if source in configured)
            if selected:
                return selected
            return configured[:1] or ("search",)

        preferred = ("feed",) if requested_limit <= 3 else ("hot", "feed")
        selected = tuple(source for source in preferred if source in configured)
        if selected:
            return selected

        non_search = tuple(source for source in configured if source != "search")
        if non_search:
            return non_search[:1]
        return configured[:1] or ("search",)

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("douyin producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def build_douyin_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    discovery_engine: Any,
) -> DouyinDiscoveryProducer | None:
    """Build the runtime Douyin producer if Douyin discovery is enabled."""
    dy_cfg = getattr(getattr(config, "sources", None), "douyin", None)
    if dy_cfg is None or not bool(getattr(dy_cfg, "enabled", False)):
        return None
    if str(getattr(dy_cfg, "mode", "direct")).strip().lower() != "direct":
        logger.info("douyin producer disabled: unsupported mode=%r", getattr(dy_cfg, "mode", ""))
        return None
    if not hasattr(database, "conn"):
        logger.info("douyin producer disabled: database does not expose task tables")
        return None

    async def _discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        from openbiliclaw.discovery.douyin import DouyinDiscoveryService
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
        from openbiliclaw.sources.douyin_direct import DouyinDirectClient
        from openbiliclaw.sources.douyin_plugin_search import DouyinPluginSearchClient

        cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
        cookie = resolve_douyin_cookie(
            data_dir=config.data_path,
            cookie_env=cookie_env,
        )
        if not cookie:
            raise RuntimeError(
                f"missing Douyin cookie; set {cookie_env} or keep the browser extension online"
            )

        async with DouyinDirectClient(cookie=cookie) as direct_client:
            client: Any = direct_client
            if any(source in options.sources for source in ("search", "hot", "feed")):
                wait_seconds = float(
                    os.environ.get("OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS", "180")
                )
                client = DouyinPluginSearchClient(
                    database=database,
                    direct_client=direct_client,
                    wait_seconds=wait_seconds,
                    daily_search_budget=int(getattr(dy_cfg, "daily_search_budget", 0)),
                    daily_hot_budget=douyin_runtime_hot_budget(
                        base_budget=int(getattr(dy_cfg, "daily_hot_budget", 0)),
                        requested_limit=options.limit,
                    ),
                    daily_feed_budget=int(getattr(dy_cfg, "daily_feed_budget", 0)),
                )
            service = DouyinDiscoveryService(
                client=client,
                discovery_engine=discovery_engine,
            )
            return await service.discover(profile, options)

    scheduler = getattr(config, "scheduler", None)
    return DouyinDiscoveryProducer(
        soul_engine=soul_engine,
        discover=_discover,
        enabled=bool(getattr(scheduler, "enabled", True)),
        min_interval_minutes=30,
        sources=("search", "hot", "feed"),
        per_source_limit=20,
    )
