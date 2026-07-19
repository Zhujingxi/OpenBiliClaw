"""Shared producer-side candidate-pool fullness gate.

Pool-share fairness (spec 2026-07-20, Phase 5 / D6). Every discovery producer
checks whether the candidate pool is full before spending budget. The naive
check hit the *global* pool, which dead-locked under-share sources (a full pool
blocked bangumi from ever producing → no bangumi supply → the share rebalancer
never fired → the pool stayed full forever). This helper centralizes the
share-aware gate so the six producers cannot drift apart:

- Prefer the pipeline's ``pool_full_for_source(source_family)`` — an under-share
  family is not "full" even when the global pool is at target.
- Fall back to the global ``pool_full()`` when the pipeline predates the
  share-aware method or the producer cannot name its family (conservative).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging

__all__ = ["candidate_pool_full_for_source"]


def candidate_pool_full_for_source(
    pipeline: Any | None,
    source_family: str | None,
    *,
    logger: logging.Logger,
    label: str,
) -> bool:
    """Return whether the candidate pool is full for *source_family*.

    ``label`` is only used to tag debug logs (e.g. ``"bangumi producer"``).
    """

    if pipeline is None:
        return False
    share_aware = getattr(pipeline, "pool_full_for_source", None)
    if callable(share_aware):
        try:
            return bool(share_aware(source_family))
        except Exception:
            logger.debug("%s: share-aware pool fullness unavailable", label, exc_info=True)
    pool_full = getattr(pipeline, "pool_full", None)
    if not callable(pool_full):
        return False
    try:
        return bool(pool_full())
    except Exception:
        logger.debug("%s: candidate pool fullness unavailable", label, exc_info=True)
        return False
