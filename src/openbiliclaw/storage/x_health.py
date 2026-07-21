"""X (Twitter) source health state machine (spec §7).

Discovery for X is server-side cookie replay; a stale cookie, a block, or a
rate-limit costs a real round-trip against the user's main account. To avoid
re-hitting x.com after a known failure, the producer persists the source's
last health state and a per-code backoff window here, then reads it back at
the top of every cycle.

States:

    ``ok``             — last call succeeded; fetch freely
    ``missing_cookie`` — no usable ``auth_token`` / ``ct0`` yet
    ``expired_cookie`` — HTTP 401: cookie expired, wait for re-login
    ``blocked``        — HTTP 403: account/endpoint forbidden, wait for re-login
    ``rate_limited``   — HTTP 429: back off until ``cooldown_until``

401 / 403 require the user to log back in on x.com (the extension re-syncs the
cookie), so the source stays "not ready" until a later success flips it back
to ``ok``. 429 sets a timed cooldown and recovers on its own; consecutive 429s
escalate that cooldown (30 min → 2 h → 6 h with the default base step) so a
persistently rate-limited account backs off instead of re-poking x.com every
30 minutes. Any successful fetch resets the escalation to the base step.

For-You is the highest-visibility (and riskiest) fetch, so it auto-pauses
after ``feed_pause_after`` consecutive For-You failures; any For-You success
lifts the pause.

State lives in a one-row ``x_source_health`` table so it survives restarts.
This module mirrors the lightweight, self-contained storage style of
``sources.x_tasks.XCreatorStore``.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.x_client import (
    XAuthError,
    XBlockedError,
    XClientError,
    XMissingCookieError,
    XRateLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

OK = "ok"
MISSING_COOKIE = "missing_cookie"
EXPIRED_COOKIE = "expired_cookie"
BLOCKED = "blocked"
RATE_LIMITED = "rate_limited"

# States that require the user to re-login on x.com before discovery can
# resume — there is no timed recovery, only a later success flips them back.
_RELOGIN_STATES = frozenset({MISSING_COOKIE, EXPIRED_COOKIE, BLOCKED})

# The singleton key (single-user model — one X account).
_ROW_KEY = "x"

# Escalating 429 cooldown ladder, applied to the base
# ``rate_limit_cooldown_minutes`` step. With the 30-min default this yields
# 30 min → 2 h → 6 h (cap). A persistently rate-limited account should back
# off progressively instead of re-poking x.com every 30 minutes forever; any
# successful fetch resets the ladder to the base step.
_COOLDOWN_MULTIPLIERS = (1, 4, 12)

# ``GET /api/sources/status`` may be served by several FastAPI worker threads
# at once.  Constructing a store used to run ``CREATE / PRAGMA / ALTER`` on the
# process-shared sqlite connection every time, so the first concurrent status
# burst could make two threads execute schema work on one connection and crash
# inside sqlite3.  Schema setup is idempotent, but the connection operation is
# not re-entrant; serialize the first setup for each Database instance and then
# keep the read path DDL-free.
_TABLE_INIT_LOCK = threading.Lock()
_TABLE_READY_ATTR = "_x_source_health_table_ready"


def health_state_for_error(exc: BaseException) -> str:
    """Map a typed :class:`XClientError` onto a discrete health state.

    Falls back to :data:`RATE_LIMITED` for an unknown ``XClientError`` (a
    transient back-off is safer than treating it as healthy) and to
    :data:`OK` for anything that is not an X error at all.
    """
    if isinstance(exc, XMissingCookieError):
        return MISSING_COOKIE
    if isinstance(exc, XAuthError):
        return EXPIRED_COOKIE
    if isinstance(exc, XBlockedError):
        return BLOCKED
    if isinstance(exc, XRateLimitError):
        return RATE_LIMITED
    if isinstance(exc, XClientError):
        return RATE_LIMITED
    return OK


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class XSourceHealthStore:
    """Persisted X source health + per-code backoff."""

    def __init__(
        self,
        db: Database,
        *,
        rate_limit_cooldown_minutes: int = 30,
        feed_pause_after: int = 3,
        credential_fingerprint: str = "",
    ) -> None:
        """*credential_fingerprint* identifies the cookie this store's successes
        belong to.

        Supplied by the producer, which resolves the cookie once and hands the
        same one to its :class:`XClient` — so a success recorded here is
        provably about the credential that made the request. Readers construct
        the store without it (they never record), and a store built without one
        records an empty fingerprint, which reads back as "cannot attribute
        this" and therefore as ``unverified``. That default errs toward
        under-claiming, which is the only safe direction for a verdict.
        """
        self._db = db
        self._rate_limit_cooldown_minutes = max(1, int(rate_limit_cooldown_minutes))
        self._feed_pause_after = max(1, int(feed_pause_after))
        self._credential_fingerprint = str(credential_fingerprint or "")
        self._ensure_table_once()

    def _ensure_table_once(self) -> None:
        if bool(getattr(self._db, _TABLE_READY_ATTR, False)):
            return
        with _TABLE_INIT_LOCK:
            if bool(getattr(self._db, _TABLE_READY_ATTR, False)):
                return
            self._ensure_table()
            setattr(self._db, _TABLE_READY_ATTR, True)

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        """Yield an isolated connection when the Database supports it.

        FastAPI serves sync status handlers in a thread pool. Python's sqlite
        connection object is not safe for concurrent operations even with
        ``check_same_thread=False``; that flag permits cross-thread ownership,
        not simultaneous use. Short-lived connections let SQLite serialize at
        the database boundary and keep status reads away from runtime writes.
        """
        open_connection = getattr(self._db, "open_connection", None)
        if callable(open_connection):
            conn = open_connection()
            try:
                yield conn
            finally:
                conn.close()
            return
        yield self._db.conn

    def _ensure_table(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS x_source_health (
                    key                  TEXT PRIMARY KEY,
                    state                TEXT NOT NULL DEFAULT 'ok',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    feed_failures        INTEGER NOT NULL DEFAULT 0,
                    feed_paused          INTEGER NOT NULL DEFAULT 0,
                    cooldown_until       TEXT NOT NULL DEFAULT '',
                    detail               TEXT NOT NULL DEFAULT '',
                    last_success_at      TEXT NOT NULL DEFAULT '',
                    last_success_credential TEXT NOT NULL DEFAULT '',
                    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Auto-migrate: older DBs predate the escalating-cooldown counter
            # and the last-success marker.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(x_source_health)").fetchall()
            }
            if "consecutive_rate_limits" not in columns:
                conn.execute(
                    "ALTER TABLE x_source_health "
                    "ADD COLUMN consecutive_rate_limits INTEGER NOT NULL DEFAULT 0"
                )
            if "last_success_credential" not in columns:
                # Also not backfilled, for the same reason as
                # ``last_success_at``: an old row records that *a* success
                # happened, never which cookie earned it.
                conn.execute(
                    "ALTER TABLE x_source_health "
                    "ADD COLUMN last_success_credential TEXT NOT NULL DEFAULT ''"
                )
            if "last_success_at" not in columns:
                # Deliberately NOT backfilled from ``updated_at``. Nothing in
                # an existing row distinguishes "a fetch succeeded" from "the
                # row was created"; guessing would recreate the false verdict.
                conn.execute(
                    "ALTER TABLE x_source_health "
                    "ADD COLUMN last_success_at TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "INSERT OR IGNORE INTO x_source_health (key, state) VALUES (?, 'ok')",
                (_ROW_KEY,),
            )
            conn.commit()

    # ── reads ────────────────────────────────────────────────────────

    def get(self) -> dict[str, Any]:
        """Return the current health row as a JSON-friendly dict.

        ``last_success_at`` is the one field that separates "a real request
        succeeded with this cookie" from "this row has never been used". The
        row is *created* with ``state='ok'``, so ``state`` alone cannot tell
        them apart — and reading ``ok`` as a verdict is how a never-used, even
        long-expired cookie came to report ``verification="verified"`` on the
        status endpoint (invariant I3).
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM x_source_health WHERE key = ?",
                (_ROW_KEY,),
            ).fetchone()
        if row is None:
            return {
                "state": OK,
                "consecutive_failures": 0,
                "feed_paused": False,
                "cooldown_until": "",
                "detail": "",
                "last_success_at": "",
                "last_success_credential": "",
                "updated_at": "",
            }
        data = dict(row)
        return {
            "state": str(data.get("state") or OK),
            "consecutive_failures": int(data.get("consecutive_failures") or 0),
            "feed_paused": bool(data.get("feed_paused")),
            "cooldown_until": str(data.get("cooldown_until") or ""),
            "detail": str(data.get("detail") or ""),
            "last_success_at": str(data.get("last_success_at") or ""),
            "last_success_credential": str(data.get("last_success_credential") or ""),
            "updated_at": str(data.get("updated_at") or ""),
        }

    def is_ready(self) -> bool:
        """Return True when the source may fetch right now.

        ``ok`` is always ready. ``rate_limited`` becomes ready once its
        cooldown window has elapsed. Re-login states (``missing_cookie`` /
        ``expired_cookie`` / ``blocked``) stay not-ready until a later success.
        """
        health = self.get()
        state = health["state"]
        if state == OK:
            return True
        if state in _RELOGIN_STATES:
            return False
        if state == RATE_LIMITED:
            cooldown = _parse_iso(health["cooldown_until"])
            if cooldown is None:
                return True
            return _now() >= cooldown
        return True

    def feed_allowed(self) -> bool:
        """Return True when For-You is not auto-paused."""
        return not self.get()["feed_paused"]

    # ── writes ───────────────────────────────────────────────────────

    def record_success(self, *, strategy: str = "") -> None:
        """Reset to ``ok`` after a successful fetch.

        Any success clears the global failure counter and cooldown. A
        For-You success additionally lifts the For-You auto-pause.
        """
        feed_clear = self._is_feed(strategy)
        # The only writer of ``last_success_at`` / ``last_success_credential``.
        # A real request came back clean, which is the sole evidence the
        # ``passive_health`` verify method may rest on — and the fingerprint
        # records *whose* evidence it is. Without that second half the marker
        # says "this platform succeeded once", so swapping in a brand-new cookie
        # silently inherited the previous one's verdict, timestamp and all.
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE x_source_health
                   SET state = 'ok',
                       consecutive_failures = 0,
                       consecutive_rate_limits = 0,
                       cooldown_until = '',
                       detail = '',
                       last_success_at = ?,
                       last_success_credential = ?,
                       feed_failures = CASE WHEN ? THEN 0 ELSE feed_failures END,
                       feed_paused = CASE WHEN ? THEN 0 ELSE feed_paused END,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE key = ?
                """,
                (
                    _now().isoformat(),
                    self._credential_fingerprint,
                    1 if feed_clear else 0,
                    1 if feed_clear else 0,
                    _ROW_KEY,
                ),
            )
            conn.commit()

    def clear_relogin_block(self) -> bool:
        """Clear a re-login block after a fresh valid cookie is synced.

        Re-login states (``missing_cookie`` / ``expired_cookie`` / ``blocked``)
        have no timed recovery: :meth:`is_ready` parks the producer, so it can
        never earn the "later success" that would reset them. A new browser
        cookie *is* that external re-login signal, so reset to ``ok`` here —
        otherwise discovery stays dead-locked even after the user re-logs in.

        Leaves ``rate_limited`` untouched (its cooldown is time-based, not a
        cookie problem). Also lifts any For-You auto-pause, since the failures
        that tripped it were attributable to the same expired session. Returns
        True when a block was actually cleared.

        ``last_success_at`` is *cleared*, not preserved: the reset is an
        optimistic unblock granted on the strength of a new credential, not a
        result obtained with it. Any earlier success belonged to the cookie
        that just got replaced, and letting it stand would hand the new one a
        verified badge it has done nothing to earn.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE x_source_health
                   SET state = 'ok',
                       consecutive_failures = 0,
                       consecutive_rate_limits = 0,
                       feed_failures = 0,
                       feed_paused = 0,
                       cooldown_until = '',
                       detail = '',
                       last_success_at = '',
                       last_success_credential = '',
                       updated_at = CURRENT_TIMESTAMP
                 WHERE key = ?
                   AND state IN ('missing_cookie', 'expired_cookie', 'blocked')
                """,
                (_ROW_KEY,),
            )
            conn.commit()
            return bool(cursor.rowcount)

    def record_error(self, exc: BaseException, *, strategy: str = "") -> str:
        """Map an error to a health state, persist it, and return the state."""
        state = health_state_for_error(exc)
        cooldown_until = ""
        # Escalate the cooldown on CONSECUTIVE 429s; any other outcome (success
        # or a re-login error) resets the ladder back to the base step.
        new_rate_limits = 0
        is_feed = self._is_feed(strategy)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            raw = conn.execute(
                """
                SELECT consecutive_rate_limits, feed_failures, feed_paused
                  FROM x_source_health
                 WHERE key = ?
                """,
                (_ROW_KEY,),
            ).fetchone()
            previous_rate_limits = int(raw["consecutive_rate_limits"]) if raw is not None else 0
            feed_failures = int(raw["feed_failures"]) if raw is not None else 0
            feed_paused = bool(raw["feed_paused"]) if raw is not None else False
            if state == RATE_LIMITED:
                new_rate_limits = previous_rate_limits + 1
                step = min(new_rate_limits - 1, len(_COOLDOWN_MULTIPLIERS) - 1)
                cooldown_minutes = self._rate_limit_cooldown_minutes * _COOLDOWN_MULTIPLIERS[step]
                cooldown_until = (_now() + timedelta(minutes=cooldown_minutes)).isoformat()
                if new_rate_limits >= 2:
                    logger.warning(
                        "X source hit %d consecutive rate limits; escalating cooldown to %d min "
                        "(resets on next successful fetch)",
                        new_rate_limits,
                        cooldown_minutes,
                    )
            if is_feed:
                feed_failures += 1
            feed_paused = feed_paused or (is_feed and feed_failures >= self._feed_pause_after)
            conn.execute(
                """
                UPDATE x_source_health
                   SET state = ?,
                       consecutive_failures = consecutive_failures + 1,
                       consecutive_rate_limits = ?,
                       feed_failures = ?,
                       feed_paused = ?,
                       cooldown_until = ?,
                       detail = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE key = ?
                """,
                (
                    state,
                    new_rate_limits,
                    feed_failures,
                    1 if feed_paused else 0,
                    cooldown_until,
                    str(exc)[:500],
                    _ROW_KEY,
                ),
            )
            conn.commit()
        return state

    def set_cooldown_until(self, value: str) -> None:
        """Override the cooldown timestamp (test seam / manual recovery)."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE x_source_health SET cooldown_until = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE key = ?",
                (str(value or ""), _ROW_KEY),
            )
            conn.commit()

    @staticmethod
    def _is_feed(strategy: str) -> bool:
        s = str(strategy or "").strip().lower()
        return s in {"feed", "for_you", "for-you", "foryou", "x-feed"}
