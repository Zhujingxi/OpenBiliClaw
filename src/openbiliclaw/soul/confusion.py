"""Confusion objects — the "看不懂" cognitive object (Phase 2).

A confusion is raised when the system observes a behaviour it cannot cleanly
read. There are two producing sources:

- **Awareness** (`build_awareness_with_confusions_prompt`): the 12h cognition
  cycle asks the awareness model to flag observations it is genuinely unsure
  how to interpret, alongside the usual notes.
- **Speculation stalemate**: when a speculation expires with a *partial*
  confirmation (``0 < confirmation_count < threshold``) — the user neither
  clearly took nor clearly rejected the probe — the ambiguity itself is a
  confusion (`InterestSpeculator` exposes the stalemate via its tick result).

A confusion NEVER writes the profile directly (spec §invariant 8). It only
drives downstream clarification (ask / probe / wait) and a topic-freeze reflex.
The state machine is::

    open ──claim──▶ clarifying ──▶ resolved | dismissed
      │                              (three exits: promote / direct-settle /
      └──────── expire (TTL) ──▶ expired      dismissed — see resolve())

At most one confusion may be ``clarifying`` at a time — enforced across
connections by a partial unique index (the durable ask budget).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.soul.ledger import ProfileLedger

logger = logging.getLogger(__name__)

# --- Calibration constants ------------------------------------------------
# Confusion candidates per awareness round. Kept tiny so a chatty model cannot
# flood the ask budget; first-round recalibration flagged (pitfall #3).
MAX_CONFUSION_CANDIDATES_PER_ROUND = 2
# Ask cooldown: once a confusion has been asked, do not re-ask for 72h. Persisted
# in the row (``asked_at``) so it survives restarts. Calibrated to the single-user
# interrupt budget (≤1 ask / 3 days); revisit after provider swap.
ASK_COOLDOWN_HOURS = 72
# Wait-path TTL: a confusion parked on "wait" self-expires after 14 days if no
# behavioural signal resolves it. Matches the probe wait window.
WAIT_TTL_DAYS = 14
# Held-update replay attempt ceiling — a replay that keeps failing is discarded
# rather than retried forever (prefer under- to double-counting, r5/R4-1).
MAX_REPLAY_ATTEMPTS = 2

_VALID_STATUSES = frozenset({"open", "clarifying", "resolved", "dismissed", "expired"})
# Resolution outcome whitelist (drives held-update replay vs discard).
_RESOLUTION_REAL_INTEREST = "real_interest"
_RESOLUTION_PROXY = "proxy_behavior"
_RESOLUTION_DISMISSED = "dismissed"
_VALID_RESOLUTIONS = frozenset(
    {_RESOLUTION_REAL_INTEREST, _RESOLUTION_PROXY, _RESOLUTION_DISMISSED}
)

_HELD_STATES = frozenset({"held", "replaying", "applied", "applied_unverified", "discarded"})


@dataclass
class HeldUpdate:
    """A topic weight change deferred while its topic is frozen.

    Freezing prevents *further reinforcement* of a confused topic: new topics
    and weight upgrades are parked here (existing weights are not rolled back).
    On resolution the held update is either replayed (rebased into the next
    preference analysis) or discarded, tracked by ``state``.
    """

    held_id: str
    topic: str
    kind: str = "new"  # "new" | "upgrade"
    value: float = 0.0
    prev_value: float = 0.0
    state: str = "held"  # held | replaying | applied | applied_unverified | discarded
    replay_submitted_at: str = ""
    batch_id: str = ""
    replay_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "held_id": self.held_id,
            "topic": self.topic,
            "kind": self.kind,
            "value": self.value,
            "prev_value": self.prev_value,
            "state": self.state,
            "replay_submitted_at": self.replay_submitted_at,
            "batch_id": self.batch_id,
            "replay_attempts": self.replay_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeldUpdate:
        state = str(data.get("state", "held"))
        if state not in _HELD_STATES:
            state = "held"
        return cls(
            held_id=str(data.get("held_id", "") or uuid.uuid4().hex[:12]),
            topic=str(data.get("topic", "")),
            kind=str(data.get("kind", "new")),
            value=_to_float(data.get("value", 0.0)),
            prev_value=_to_float(data.get("prev_value", 0.0)),
            state=state,
            replay_submitted_at=str(data.get("replay_submitted_at", "")),
            batch_id=str(data.get("batch_id", "")),
            replay_attempts=int(_to_int(data.get("replay_attempts", 0))),
        )


@dataclass
class Confusion:
    """In-memory view of a ``confusions`` row."""

    id: int
    status: str = "open"
    source: str = ""
    topic: str = ""
    observation: str = ""
    interpretation: str = ""
    interpretation_confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    resolution: str = ""
    resolution_note: str = ""
    asked_at: str = ""
    ask_turn_id: str = ""
    defer_count: int = 0
    expires_at: str = ""
    held_updates: list[HeldUpdate] = field(default_factory=list)
    resolved_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Confusion:
        held = [
            HeldUpdate.from_dict(item)
            for item in row.get("held_updates", [])
            if isinstance(item, dict)
        ]
        refs = [str(r) for r in row.get("evidence_refs", []) if str(r)]
        return cls(
            id=int(row.get("id", 0)),
            status=str(row.get("status", "open") or "open"),
            source=str(row.get("source", "") or ""),
            topic=str(row.get("topic", "") or ""),
            observation=str(row.get("observation", "") or ""),
            interpretation=str(row.get("interpretation", "") or ""),
            interpretation_confidence=_to_float(row.get("interpretation_confidence", 0.0)),
            evidence_refs=refs,
            resolution=str(row.get("resolution", "") or ""),
            resolution_note=str(row.get("resolution_note", "") or ""),
            asked_at=str(row.get("asked_at") or ""),
            ask_turn_id=str(row.get("ask_turn_id", "") or ""),
            defer_count=int(_to_int(row.get("defer_count", 0))),
            expires_at=str(row.get("expires_at") or ""),
            held_updates=held,
            resolved_at=str(row.get("resolved_at") or ""),
        )


class ConfusionManager:
    """State machine + DAO wrapper for confusion objects.

    Constructed with a ``database`` handle (may be ``None`` — a headless
    component or test without storage — in which case every operation is a
    silent no-op returning empty/false). ``ledger`` (optional) records every
    state transition into ``profile_update_ledger`` (best-effort observer).
    """

    def __init__(
        self,
        database: Any | None,
        ledger: ProfileLedger | None = None,
    ) -> None:
        self._db = database
        self._ledger = ledger

    # -- Producing sources ----------------------------------------------------

    def create_from_awareness_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[int]:
        """Validate + persist awareness-sourced confusion candidates.

        Whitelist/clamp (pitfall #4): drop candidates without an ``observation``;
        cap the batch at ``MAX_CONFUSION_CANDIDATES_PER_ROUND`` (excess logged).
        """
        if self._db is None or not candidates:
            return []
        valid = [
            c for c in candidates if isinstance(c, dict) and str(c.get("observation", "")).strip()
        ]
        if len(valid) > MAX_CONFUSION_CANDIDATES_PER_ROUND:
            logger.warning(
                "Awareness produced %d confusion candidates; capping at %d",
                len(valid),
                MAX_CONFUSION_CANDIDATES_PER_ROUND,
            )
            valid = valid[:MAX_CONFUSION_CANDIDATES_PER_ROUND]
        created: list[int] = []
        for cand in valid:
            cid = self._db.insert_confusion(
                source="awareness",
                topic=str(cand.get("topic", "")).strip(),
                observation=str(cand.get("observation", "")).strip(),
                interpretation=str(cand.get("interpretation", "")).strip(),
                interpretation_confidence=_clamp01(cand.get("interpretation_confidence", 0.0)),
                evidence_refs=[str(r) for r in _as_list(cand.get("evidence_refs")) if str(r)],
            )
            if cid:
                created.append(cid)
                self._record("confusion_open", topic=str(cand.get("topic", "")), after={"id": cid})
        return created

    def create_from_speculation_stalemate(
        self,
        *,
        domain: str,
        confirmation_count: int,
        confirmation_threshold: int,
        evidence_refs: list[str] | None = None,
    ) -> int | None:
        """Raise a confusion for a speculation that expired partially confirmed.

        The caller has already filtered ``0 < confirmation_count < threshold``
        (spec §Phase 2 decidable-stalemate); this method persists the object.
        """
        if self._db is None or not domain.strip():
            return None
        observation = (
            f"对「{domain}」的猜测得到过 {confirmation_count} 次部分确认，"
            f"但未达到 {confirmation_threshold} 次门槛就到期了——你对它到底有没有兴趣不清楚。"
        )
        cid = self._db.insert_confusion(
            source="speculation_stalemate",
            topic=domain.strip(),
            observation=observation,
            interpretation="部分确认但未达门槛，可能是弱兴趣或误判。",
            interpretation_confidence=round(confirmation_count / max(1, confirmation_threshold), 4),
            evidence_refs=[str(r) for r in (evidence_refs or []) if str(r)],
        )
        if cid:
            self._record("confusion_open", topic=domain, after={"id": cid, "source": "stalemate"})
        return cid or None

    # -- Reads ----------------------------------------------------------------

    def get(self, confusion_id: int) -> Confusion | None:
        if self._db is None:
            return None
        row = self._db.get_confusion(confusion_id)
        return Confusion.from_row(row) if row is not None else None

    def list_open(self) -> list[Confusion]:
        return self._list(["open"])

    def list_active(self) -> list[Confusion]:
        """Open + clarifying confusions (injected into the dialogue active list)."""
        return self._list(["open", "clarifying"])

    def _list(self, statuses: list[str]) -> list[Confusion]:
        if self._db is None:
            return []
        return [Confusion.from_row(r) for r in self._db.list_confusions(statuses=statuses)]

    def frozen_topics(self) -> set[str]:
        """Topics with an unresolved confusion — frozen against reinforcement."""
        return {c.topic.strip() for c in self.list_active() if c.topic.strip()}

    # -- TTL maintenance (folded into the 12h cognition cycle) ----------------

    def expire_due(self, *, now: datetime | None = None) -> list[int]:
        """Expire open/clarifying confusions past their wait TTL.

        Returns the ids expired. Held updates on an expired confusion are
        discarded (dismissed/expired → discard, spec §Phase 2 unfreeze).
        """
        if self._db is None:
            return []
        current = now or datetime.now()
        expired: list[int] = []
        for confusion in self._list(["open", "clarifying"]):
            if not confusion.expires_at:
                continue
            due = _parse_iso(confusion.expires_at)
            if due is None or current < due:
                continue
            self._discard_all_held(confusion)
            self._db.update_confusion(
                confusion.id,
                status="expired",
                resolved_at=current.isoformat(),
                held_updates=[h.to_dict() for h in confusion.held_updates],
            )
            expired.append(confusion.id)
            self._record("confusion_expired", topic=confusion.topic, before={"id": confusion.id})
        return expired

    # -- Internal helpers -----------------------------------------------------

    def _discard_all_held(self, confusion: Confusion) -> None:
        for held in confusion.held_updates:
            if held.state not in {"applied", "applied_unverified"}:
                held.state = "discarded"

    def _record(
        self,
        write_point: str,
        *,
        topic: str = "",
        before: object = None,
        after: object = None,
        held_id: str = "",
    ) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.record(
                write_point=write_point,
                source="confusion",
                before=before,
                after=after,
                source_refs=[topic] if topic else [],
                held_id=held_id,
            )
        except Exception:  # pragma: no cover - ledger is best-effort
            logger.debug("confusion ledger record failed", exc_info=True)


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return 0


def _clamp01(value: object) -> float:
    return max(0.0, min(1.0, round(_to_float(value), 4)))


def _parse_iso(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
