"""Best-effort append-only ledger for profile write points (Phase 0).

The cognitive-profile pipeline requires every profile-mutating write point
to be auditable. :class:`ProfileLedger` records one row per action *after*
the action finishes, tagged with its ``outcome`` (``success`` | ``failed``)
and compact before/after summaries plus a diff.

Design invariants (see spec §Design invariants 4):

- **Only append.** No updates; a rollback is expressed as a later
  compensating row.
- **Best-effort observer.** A ledger write failure is logged at WARNING and
  never blocks the underlying profile update. The coverage target is
  "the hook exists", not "the row was necessarily written".
- **Single INSERT per action** (r3/R2-6). The :meth:`ProfileLedger.action`
  context manager records exactly one row when the wrapped block exits —
  ``success`` normally, ``failed`` (and then re-raises) on exception. It
  never writes a separate "attempted" pre-row.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

logger = logging.getLogger(__name__)

# Diff/summary payloads are audit context, not the source of truth — cap them
# so a pathological profile blob cannot bloat the ledger. 2000 chars comfortably
# holds a top-level key diff of the preference/soul dicts we record.
_LEDGER_TEXT_MAX_CHARS = 2000


def summarize_for_ledger(value: object) -> str:
    """Serialize ``value`` to a compact, deterministic, length-capped string."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > _LEDGER_TEXT_MAX_CHARS:
        text = text[:_LEDGER_TEXT_MAX_CHARS]
    return text


def diff_for_ledger(before: object, after: object) -> str:
    """Return a compact top-level diff of two dicts (or a summary fallback)."""
    if isinstance(before, dict) and isinstance(after, dict):
        changed_keys: list[str] = []
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                changed_keys.append(str(key))
        text = json.dumps(
            {"changed_keys": changed_keys},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    else:
        text = json.dumps(
            {"before": summarize_for_ledger(before), "after": summarize_for_ledger(after)},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    if len(text) > _LEDGER_TEXT_MAX_CHARS:
        text = text[:_LEDGER_TEXT_MAX_CHARS]
    return text


@dataclass
class LedgerEntry:
    """Mutable handle yielded by :meth:`ProfileLedger.action`.

    The caller sets :attr:`after` (and optionally extends
    :attr:`source_refs`) once the write completes; the context manager reads
    these when it records the final row.
    """

    after: Any = None
    source_refs: list[str] = field(default_factory=list)
    turn_id: str = ""
    gate_verdict: str = ""
    held_id: str = ""


class ProfileLedger:
    """Records profile write points to ``profile_update_ledger``.

    Constructed with a ``database`` handle (may be ``None`` — e.g. tests or
    a headless component — in which case every record is a silent no-op).
    """

    def __init__(self, database: Any | None) -> None:
        self._database = database

    def record(
        self,
        *,
        write_point: str,
        source: str = "",
        before: object = None,
        after: object = None,
        source_refs: Sequence[str] | None = None,
        outcome: str = "success",
        turn_id: str = "",
        gate_verdict: str = "",
        held_id: str = "",
        error: str = "",
        effect_key: str = "",
    ) -> None:
        """Append one ledger row. Never raises — failures log at WARNING."""
        db = self._database
        if db is None:
            return
        insert = getattr(db, "insert_profile_ledger", None)
        if not callable(insert):
            return
        try:
            insert(
                write_point=str(write_point),
                source=str(source or ""),
                before_summary=summarize_for_ledger(before),
                after_summary=summarize_for_ledger(after),
                diff=diff_for_ledger(before, after),
                source_refs=[str(ref) for ref in (source_refs or [])],
                outcome=str(outcome or "success"),
                turn_id=str(turn_id or ""),
                gate_verdict=str(gate_verdict or ""),
                held_id=str(held_id or ""),
                error=str(error or ""),
                effect_key=str(effect_key or ""),
            )
        except Exception:
            logger.warning(
                "profile ledger write failed for write_point=%s (best-effort, ignored)",
                write_point,
                exc_info=True,
            )

    @contextlib.contextmanager
    def action(
        self,
        *,
        write_point: str,
        source: str = "",
        before: object = None,
        source_refs: Sequence[str] | None = None,
        turn_id: str = "",
    ) -> Iterator[LedgerEntry]:
        """Wrap a profile write; record exactly one row when the block exits.

        On success records ``outcome='success'``; on exception records
        ``outcome='failed'`` (capturing the error message) and re-raises so
        the caller's existing control flow is unchanged.
        """
        entry = LedgerEntry(
            after=before,
            source_refs=[str(ref) for ref in (source_refs or [])],
            turn_id=turn_id,
        )
        try:
            yield entry
        except Exception as exc:
            self.record(
                write_point=write_point,
                source=source,
                before=before,
                after=entry.after,
                source_refs=entry.source_refs,
                outcome="failed",
                turn_id=entry.turn_id,
                gate_verdict=entry.gate_verdict,
                held_id=entry.held_id,
                error=str(exc),
            )
            raise
        else:
            self.record(
                write_point=write_point,
                source=source,
                before=before,
                after=entry.after,
                source_refs=entry.source_refs,
                outcome="success",
                turn_id=entry.turn_id,
                gate_verdict=entry.gate_verdict,
                held_id=entry.held_id,
            )
