"""Persistent single-anchor state for dialogue confirmation threads."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.memory.json_state import read_json_state, update_json_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.soul.ledger import ProfileLedger

logger = logging.getLogger(__name__)

ENTRY_CONFUSION_PROMPT = "confusion_prompt"
ENTRY_CARD_DISCUSS = "card_discuss"
ENTRY_PENDING_OPEN = "pending_open"
_VALID_ENTRIES = frozenset(
    {
        ENTRY_CONFUSION_PROMPT,
        ENTRY_CARD_DISCUSS,
        ENTRY_PENDING_OPEN,
    }
)
_VALID_KINDS = frozenset({"hypothesis", "confusion"})
_VALID_RELEASE_REASONS = frozenset({"settled", "unrelated", "ttl", "replaced"})
_TERMINAL_CARD_STATES = frozenset({"confirmed", "rejected", "revised", "deferred"})

# First-round calibration (2026-07-22): two hours covers one complete focused
# discussion without letting yesterday's topic capture a later conversation.
# Recalibrate after the first production month or a dialogue-model swap.
ANCHOR_TTL_HOURS = 2
_UNRELATED_RELEASE_TURNS = 2
_STATE_FILENAME = "dialogue_anchor_state.json"


def _normalized_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.strip().replace("Z", "+00:00")
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _normalized_now(parsed)


@dataclass(frozen=True)
class DialogueAnchor:
    """One durable topic anchor; at most one is stored at a time."""

    kind: str
    ref: str
    generation: int
    established_at: str
    unrelated_streak: int = 0
    origin_turn_id: str = ""
    ambiguous_count: int = 0

    @classmethod
    def from_dict(cls, raw: object) -> DialogueAnchor | None:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind", "")).strip()
        ref = str(raw.get("ref", "")).strip()
        try:
            generation = int(raw.get("generation", 0))
            unrelated_streak = max(0, int(raw.get("unrelated_streak", 0)))
            ambiguous_count = max(0, int(raw.get("ambiguous_count", 0)))
        except (TypeError, ValueError):
            return None
        established_at = str(raw.get("established_at", "")).strip()
        if (
            kind not in _VALID_KINDS
            or not ref
            or generation <= 0
            or _parse_timestamp(established_at) is None
        ):
            return None
        return cls(
            kind=kind,
            ref=ref,
            generation=generation,
            established_at=established_at,
            unrelated_streak=unrelated_streak,
            origin_turn_id=str(raw.get("origin_turn_id", "")).strip(),
            ambiguous_count=ambiguous_count,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _default_state() -> dict[str, Any]:
    return {"generation": 0, "anchor": None}


def _normalize_state(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _default_state()
    anchor = DialogueAnchor.from_dict(raw.get("anchor"))
    try:
        generation = max(0, int(raw.get("generation", 0)))
    except (TypeError, ValueError):
        generation = 0
    if anchor is not None:
        generation = max(generation, anchor.generation)
    return {
        "generation": generation,
        "anchor": anchor.to_dict() if anchor is not None else None,
    }


class DialogueAnchorManager:
    """Persist and fence the one active confirmation-dialogue anchor."""

    def __init__(
        self,
        data_dir: str | Path | None,
        *,
        database: Any | None = None,
        ledger: ProfileLedger | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_path = (
            Path(data_dir) / "memory" / _STATE_FILENAME if data_dir is not None else None
        )
        self._database = database
        self._ledger = ledger
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._volatile_state = _default_state()
        self._volatile_lock = threading.RLock()
        self._mutation_guard: Callable[[], None] | None = None

    def install_mutation_guard(self, guard: Callable[[], None]) -> None:
        """Require ``guard`` for runtime mutations while keeping low-level tests injectable."""
        if self._mutation_guard is not None and self._mutation_guard != guard:
            raise RuntimeError("Dialogue anchor mutation guard is already installed")
        self._mutation_guard = guard

    def _require_dialogue_settlement_worker(self) -> None:
        guard = self._mutation_guard
        if guard is not None:
            guard()

    @property
    def database(self) -> Any | None:
        """Expose the shared store for lifecycle diagnostics and tests."""
        return self._database

    def current(self) -> DialogueAnchor | None:
        """Return the persisted anchor, tolerating a missing/corrupt state file."""
        state = self._read_state()
        return DialogueAnchor.from_dict(state.get("anchor"))

    def snapshot(self) -> dict[str, object]:
        """Return the exact typed anchor generation captured at admission."""
        anchor = self.current()
        if anchor is None:
            return {
                "anchor_kind": "",
                "anchor_ref": "",
                "anchor_generation": 0,
            }
        return {
            "anchor_kind": anchor.kind,
            "anchor_ref": anchor.ref,
            "anchor_generation": anchor.generation,
        }

    def establish(
        self,
        *,
        kind: str,
        ref: str,
        origin_turn_id: str,
        entry: str,
        now: datetime | None = None,
    ) -> DialogueAnchor:
        """Establish an anchor from one of the three declared product entries."""
        self._require_dialogue_settlement_worker()
        normalized_kind = kind.strip().lower()
        normalized_ref = ref.strip()
        normalized_entry = entry.strip().lower()
        if normalized_kind not in _VALID_KINDS:
            raise ValueError(f"Unsupported dialogue anchor kind: {kind!r}")
        if not normalized_ref:
            raise ValueError("Dialogue anchor ref is required")
        if normalized_entry not in _VALID_ENTRIES:
            raise ValueError(f"Unsupported dialogue anchor entry: {entry!r}")
        established_at = _normalized_now(now or self._now_provider()).isoformat()
        released: DialogueAnchor | None = None
        released_replays: list[dict[str, Any]] = []
        established: DialogueAnchor | None = None
        created_new = False

        def mutate(state: dict[str, Any]) -> None:
            nonlocal released, established, created_new
            existing = DialogueAnchor.from_dict(state.get("anchor"))
            normalized_origin = origin_turn_id.strip()
            if (
                existing is not None
                and existing.kind == normalized_kind
                and existing.ref == normalized_ref
            ):
                established = existing
                return
            if existing is not None:
                released_replays.extend(
                    self._prepare_release(existing, reason="replaced", card_state="")
                )
                released = existing
            generation = int(state.get("generation", 0)) + 1
            established = DialogueAnchor(
                kind=normalized_kind,
                ref=normalized_ref,
                generation=generation,
                established_at=established_at,
                origin_turn_id=normalized_origin,
            )
            state["generation"] = generation
            state["anchor"] = established.to_dict()
            created_new = True

        self._mutate_state(mutate)
        if released is not None:
            self._record_release(released, "replaced")
            self._record_replay_drops(released, released_replays, reason="replaced")
        if established is None:  # pragma: no cover - guarded by mutation above
            raise RuntimeError("Failed to establish dialogue anchor")
        if created_new:
            self._record_established(established, normalized_entry)
        return established

    def release(
        self,
        *,
        reason: str,
        card_state: str = "",
        expected_generation: int | None = None,
    ) -> DialogueAnchor | None:
        """Release the anchor for one of the four declared lifecycle reasons."""
        self._require_dialogue_settlement_worker()
        normalized_reason = reason.strip().lower()
        normalized_card_state = card_state.strip().lower()
        if normalized_reason not in _VALID_RELEASE_REASONS:
            raise ValueError(f"Unsupported dialogue anchor release reason: {reason!r}")
        if normalized_card_state and normalized_card_state not in _TERMINAL_CARD_STATES:
            raise ValueError(f"Unsupported terminal card state: {card_state!r}")
        released: DialogueAnchor | None = None
        released_replays: list[dict[str, Any]] = []

        def mutate(state: dict[str, Any]) -> None:
            nonlocal released
            anchor = DialogueAnchor.from_dict(state.get("anchor"))
            if anchor is None:
                return
            if expected_generation is not None and anchor.generation != expected_generation:
                logger.warning(
                    "dialogue anchor release fenced: expected generation=%s actual=%s",
                    expected_generation,
                    anchor.generation,
                )
                return
            released_replays.extend(
                self._prepare_release(
                    anchor,
                    reason=normalized_reason,
                    card_state=normalized_card_state,
                )
            )
            released = anchor
            state["anchor"] = None

        self._mutate_state(mutate)
        if released is not None:
            self._record_release(released, normalized_reason)
            self._record_replay_drops(
                released,
                released_replays,
                reason=normalized_reason,
            )
        return released

    def note_relation(
        self,
        relation: str,
        *,
        expected_generation: int | None = None,
    ) -> DialogueAnchor | None:
        """Persist unrelated/ambiguous counters and release after two unrelated turns."""
        self._require_dialogue_settlement_worker()
        normalized_relation = relation.strip().lower()
        released: DialogueAnchor | None = None
        updated: DialogueAnchor | None = None
        released_replays: list[dict[str, Any]] = []
        generation_matched = False

        def mutate(state: dict[str, Any]) -> None:
            nonlocal generation_matched, released, updated
            anchor = DialogueAnchor.from_dict(state.get("anchor"))
            if anchor is None:
                return
            if expected_generation is not None and anchor.generation != expected_generation:
                logger.warning(
                    "dialogue anchor relation fenced: expected generation=%s actual=%s",
                    expected_generation,
                    anchor.generation,
                )
                return
            generation_matched = True
            if normalized_relation == "unrelated":
                next_anchor = replace(
                    anchor,
                    unrelated_streak=anchor.unrelated_streak + 1,
                    ambiguous_count=0,
                )
                if next_anchor.unrelated_streak >= _UNRELATED_RELEASE_TURNS:
                    released_replays.extend(
                        self._prepare_release(next_anchor, reason="unrelated", card_state="")
                    )
                    released = next_anchor
                    state["anchor"] = None
                else:
                    updated = next_anchor
                    state["anchor"] = next_anchor.to_dict()
                return
            if normalized_relation == "ambiguous":
                next_anchor = replace(
                    anchor,
                    unrelated_streak=0,
                    ambiguous_count=anchor.ambiguous_count + 1,
                )
            else:
                next_anchor = replace(anchor, unrelated_streak=0, ambiguous_count=0)
            updated = next_anchor
            state["anchor"] = next_anchor.to_dict()

        self._mutate_state(mutate)
        if released is not None:
            self._record_release(released, "unrelated")
            self._record_replay_drops(released, released_replays, reason="unrelated")
            return None
        if not generation_matched:
            return None
        return updated

    def expire(self, *, now: datetime | None = None) -> bool:
        """Release an anchor whose absolute two-hour TTL has elapsed."""
        self._require_dialogue_settlement_worker()
        anchor = self.current()
        if anchor is None:
            return False
        established = _parse_timestamp(anchor.established_at)
        if established is None:
            logger.warning("dialogue anchor has invalid established_at; releasing by TTL")
        else:
            current = _normalized_now(now or self._now_provider())
            if current - established < timedelta(hours=ANCHOR_TTL_HOURS):
                return False
        return self.release(reason="ttl", expected_generation=anchor.generation) is not None

    def validate_snapshot(self, anchor_ref: str, anchor_generation: int) -> DialogueAnchor | None:
        """Return the current anchor only when the queued generation still matches."""
        anchor = self.current()
        try:
            normalized_generation = int(anchor_generation)
        except (TypeError, ValueError):
            normalized_generation = -1
        if (
            anchor is not None
            and anchor.ref == anchor_ref.strip()
            and anchor.generation == normalized_generation
        ):
            return anchor
        if anchor_ref or anchor_generation:
            logger.warning(
                "stale dialogue anchor snapshot dropped: ref=%r generation=%s",
                anchor_ref,
                anchor_generation,
            )
        return None

    def _read_state(self) -> dict[str, Any]:
        path = self._state_path
        if path is None:
            with self._volatile_lock:
                return _normalize_state(self._volatile_state)
        return read_json_state(
            path,
            default_factory=_default_state,
            normalize=_normalize_state,
        )

    def _mutate_state(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        path = self._state_path
        if path is None:
            with self._volatile_lock:
                state = _normalize_state(self._volatile_state)
                mutate(state)
                self._volatile_state = _normalize_state(state)
                return _normalize_state(self._volatile_state)

        def apply(state: dict[str, Any]) -> dict[str, Any]:
            mutate(state)
            return state

        return update_json_state(
            path,
            default_factory=_default_state,
            normalize=_normalize_state,
            serialize=lambda state: state,
            mutate=apply,
        )

    def _prepare_release(
        self,
        anchor: DialogueAnchor,
        *,
        reason: str,
        card_state: str,
    ) -> list[dict[str, Any]]:
        if reason == "settled" and anchor.kind == "hypothesis" and not card_state:
            raise ValueError("Hypothesis anchor settlement requires a terminal card state")
        database = self._database
        if database is None:
            return []
        dropped_replays: list[dict[str, Any]] = []
        if anchor.kind == "confusion":
            clear_replay_queue = getattr(database, "clear_confusion_replay_queue", None)
            if callable(clear_replay_queue):
                try:
                    confusion_id = int(anchor.ref.rsplit(":", maxsplit=1)[-1])
                except ValueError:
                    logger.warning(
                        "Cannot clear replay queue for malformed anchor ref=%r", anchor.ref
                    )
                else:
                    raw_dropped = clear_replay_queue(confusion_id)
                    if isinstance(raw_dropped, list):
                        dropped_replays = [
                            dict(item) for item in raw_dropped if isinstance(item, dict)
                        ]
        update_payload = getattr(database, "update_chat_turn_payload_state", None)
        if anchor.origin_turn_id and callable(update_payload):
            if reason == "settled" and anchor.kind == "hypothesis":
                update_payload(
                    anchor.origin_turn_id,
                    expected_state="discussing",
                    new_state=card_state,
                )
            elif reason != "settled":
                update_payload(
                    anchor.origin_turn_id,
                    expected_state="discussing",
                    new_state="pending",
                )
        if anchor.kind != "confusion" or reason == "settled":
            return dropped_replays
        get_confusion = getattr(database, "get_confusion", None)
        update_confusion = getattr(database, "update_confusion", None)
        if not callable(get_confusion) or not callable(update_confusion):
            return dropped_replays
        try:
            confusion_id = int(anchor.ref.rsplit(":", maxsplit=1)[-1])
        except ValueError:
            logger.warning("Cannot reopen confusion for malformed anchor ref=%r", anchor.ref)
            return dropped_replays
        row = get_confusion(confusion_id)
        if isinstance(row, dict) and str(row.get("status", "")) == "clarifying":
            update_confusion(confusion_id, status="open")
        return dropped_replays

    def _record_established(self, anchor: DialogueAnchor, entry: str) -> None:
        if self._ledger is None:
            return
        self._ledger.record(
            write_point="anchor_established",
            source=entry,
            after=anchor.to_dict(),
            source_refs=[f"{anchor.kind}:{anchor.ref}"],
            turn_id=anchor.origin_turn_id,
        )

    def _record_release(self, anchor: DialogueAnchor, reason: str) -> None:
        if self._ledger is None:
            return
        self._ledger.record(
            write_point="anchor_released",
            source="dialogue_anchor",
            before=anchor.to_dict(),
            after={"reason": reason, "generation": anchor.generation},
            source_refs=[f"{anchor.kind}:{anchor.ref}"],
            turn_id=anchor.origin_turn_id,
        )

    def _record_replay_drops(
        self,
        anchor: DialogueAnchor,
        dropped: list[dict[str, Any]],
        *,
        reason: str,
    ) -> None:
        if self._ledger is None:
            return
        for item in dropped:
            self._ledger.record(
                write_point="confusion_replay_dropped",
                source="dialogue_anchor",
                before=item,
                after={"reason": reason, "generation": anchor.generation},
                source_refs=[f"{anchor.kind}:{anchor.ref}"],
                turn_id=str(item.get("turn_id", "")),
            )
