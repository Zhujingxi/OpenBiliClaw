"""Shared, restart-surviving cadence floor for source producers.

Douyin / YouTube / X / Zhihu / Reddit each kept "when did I last run" in a
Python attribute. That has two failure modes pulling in opposite directions,
both measured on real data before this module existed:

* **Restart is too loose.** The attribute resets to ``None`` on every backend
  start, so the very next tick fires regardless of the configured floor. In a
  25-day Reddit sample, 5 of 55 rounds landed 8 / 10 / 11 / 35 / 40 minutes
  apart while ``min_interval_minutes`` was 60.
* **An empty round is too tight.** The stamp was written on every successful
  return, including rounds that produced nothing — precisely the case that
  should be retried on the next tick rather than blocked for a full interval
  (a just-expired cookie, say, would cost a whole window after being fixed).

Both go away by keying the floor on "when did this source last actually
produce candidates", persisted in ``source_producer_runs``. Producers built
without a database (unit tests, CLI one-shots) transparently fall back to the
in-process timestamp, so nothing that used to work stops working.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ledger_available", "producer_ran_within", "record_producer_run"]


def ledger_available(database: Any) -> bool:
    """True when ``database`` can back the cadence floor across restarts."""
    if database is None:
        return False
    return callable(getattr(database, "source_producer_ran_within", None)) and callable(
        getattr(database, "record_source_producer_run", None)
    )


def producer_ran_within(database: Any, platform: str, minutes: int) -> bool:
    """Whether ``platform`` produced candidates inside the last ``minutes``.

    A lookup failure returns False (treat as due) rather than silently pinning
    the producer shut — a broken read should not be able to stall discovery.
    """
    if not ledger_available(database) or int(minutes) <= 0:
        return False
    try:
        return bool(database.source_producer_ran_within(platform, int(minutes)))
    except Exception:
        logger.exception("producer cadence lookup failed for %s", platform)
        return False


def record_producer_run(database: Any, platform: str, discovered: int) -> None:
    """Stamp a productive round. Empty rounds are deliberately not recorded."""
    if not ledger_available(database) or int(discovered) <= 0:
        return
    try:
        database.record_source_producer_run(platform, int(discovered))
    except Exception:
        logger.exception("producer cadence record failed for %s", platform)
