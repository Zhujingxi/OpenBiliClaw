"""Periodic cognition cycle — throttled awareness + insight generation.

The ProfileUpdatePipeline calls ``CognitionCycle.run_if_due()`` from its
``tick()`` loop. On each call, the cycle checks whether enough time has
passed since the last successful run (default: 12 hours) and, if so,
regenerates awareness notes and insight hypotheses via the LLM-backed
analyzers, then syncs the results into the OnionProfile so the extension
popup's profile view shows them.

State is persisted to ``<data_dir>/memory/cognition_cycle_state.json`` so
throttling survives process restarts.

This module exists to bridge a gap that was previously "orphaned": the
AwarenessAnalyzer and InsightAnalyzer were defined but had zero runtime
callers, so ``profile.recent_awareness`` and ``profile.active_insights``
were always empty. The cycle wires them into the normal tick loop with a
cost-aware throttle so LLM spend stays bounded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer
    from openbiliclaw.soul.profile import AwarenessNote, InsightHypothesis

from openbiliclaw.soul.profile import (
    OnionProfile,
    awareness_note_from_dict,
    awareness_note_to_dict,
    insight_hypothesis_from_dict,
    insight_hypothesis_to_dict,
)

logger = logging.getLogger(__name__)

# Default throttle: generate awareness+insight once every 12 hours.
DEFAULT_MIN_INTERVAL_SECONDS = 12 * 60 * 60

# How many events to feed the awareness analyzer per run.
_AWARENESS_EVENT_LIMIT = 50

# How many notes/insights to keep attached to the OnionProfile (surfaced in UI).
_PROFILE_AWARENESS_WINDOW = 8
_PROFILE_INSIGHT_WINDOW = 6


@dataclass
class CognitionCycleResult:
    """Summary of one cognition cycle run."""

    ran: bool = False
    throttled: bool = False
    awareness_generated: int = 0
    insight_generated: int = 0
    total_awareness_after: int = 0
    total_insight_after: int = 0
    errors: list[str] = field(default_factory=list)


class CognitionCycle:
    """Throttled awareness + insight generation runner.

    Usage:
        cycle = CognitionCycle(
            memory=memory,
            awareness_analyzer=...,
            insight_analyzer=...,
            min_interval_seconds=43200,
        )
        result = await cycle.run_if_due()
    """

    def __init__(
        self,
        *,
        memory: MemoryManager,
        awareness_analyzer: AwarenessAnalyzer,
        insight_analyzer: InsightAnalyzer,
        min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._memory = memory
        self._awareness_analyzer = awareness_analyzer
        self._insight_analyzer = insight_analyzer
        self._min_interval_seconds = int(min_interval_seconds)

    # -- Public API -----------------------------------------------------------

    async def run_if_due(self, *, now: datetime | None = None) -> CognitionCycleResult:
        """Run awareness+insight generation if the throttle interval has elapsed.

        Returns a result describing what happened. On throttle skip, returns
        ``CognitionCycleResult(ran=False, throttled=True)``.
        """
        current_time = now or datetime.now()
        state = self._load_state()
        result = CognitionCycleResult()

        # Gate: awareness + insight LLM calls feed on `preference` and `soul`
        # memory layers. If neither has been built yet (init's first ~7
        # minutes), the analyzer prompts get near-empty inputs and tend to
        # blow up. Silent skip here avoids the ERROR-level traces every
        # cognition tick before the profile lands, while still allowing a
        # partially initialized profile to accrue fresh awareness.
        preference_data = self._memory.get_layer("preference").data
        soul_data = self._memory.get_layer("soul").data
        if not preference_data and not soul_data:
            logger.debug("CognitionCycle skipped: preference and soul layers are empty")
            result.throttled = True
            return result

        last_awareness_at = _parse_iso(state.get("last_awareness_at"))
        last_insight_at = _parse_iso(state.get("last_insight_at"))

        awareness_due = self._is_due(last_awareness_at, current_time)
        insight_due = self._is_due(last_insight_at, current_time)

        if not awareness_due and not insight_due:
            result.throttled = True
            return result

        result.ran = True

        # 1. Awareness pass
        if awareness_due:
            try:
                added = await self._run_awareness()
                result.awareness_generated = added
                state["last_awareness_at"] = current_time.isoformat()
            except Exception as exc:
                logger.exception("Awareness analyzer failed during cognition cycle")
                result.errors.append(f"awareness: {exc}")

        # 2. Insight pass — runs after awareness so it can use the fresh notes
        if insight_due:
            try:
                added = await self._run_insight()
                result.insight_generated = added
                state["last_insight_at"] = current_time.isoformat()
            except Exception as exc:
                logger.exception("Insight analyzer failed during cognition cycle")
                result.errors.append(f"insight: {exc}")

        # 3. Sync the fresh awareness/insights into the OnionProfile so the
        # popup sees them immediately. This is a best-effort write — a
        # missing soul layer or mid-init state should not break the cycle.
        try:
            self._sync_to_profile(result)
        except Exception:
            logger.exception("Failed to sync cognition cycle output into profile")

        self._save_state(state)
        return result

    # -- Internal -------------------------------------------------------------

    def _is_due(
        self,
        last_run_at: datetime | None,
        now: datetime,
    ) -> bool:
        if last_run_at is None:
            return True
        elapsed = (now - last_run_at).total_seconds()
        return elapsed >= self._min_interval_seconds

    async def _run_awareness(self) -> int:
        """Run the awareness analyzer and persist the merged result.

        Returns the number of NEW notes added (0 if LLM returned nothing new).
        """
        events = self._memory.query_events(limit=_AWARENESS_EVENT_LIMIT)
        preference = self._memory.get_layer("preference").data
        soul_profile_data = self._memory.get_layer("soul").data

        new_notes = await self._awareness_analyzer.analyze(
            events=events,
            preference=preference,
            soul_profile=soul_profile_data,
        )
        if not new_notes:
            return 0

        existing = self._load_awareness_notes()
        merged = self._awareness_analyzer.merge_notes(existing, new_notes)
        added = len(merged) - len(existing)
        self._save_awareness_notes(merged)
        return max(0, added)

    async def _run_insight(self) -> int:
        """Run the insight analyzer and persist the merged result."""
        awareness_notes = self._load_awareness_notes()
        if not awareness_notes:
            # No awareness yet → no insights to derive. This happens on the
            # very first run when awareness also just ran and produced zero,
            # or on very quiet accounts.
            return 0
        preference = self._memory.get_layer("preference").data
        soul_profile_data = self._memory.get_layer("soul").data

        new_insights = await self._insight_analyzer.analyze(
            awareness_notes=awareness_notes,
            preference=preference,
            soul_profile=soul_profile_data,
        )
        if not new_insights:
            return 0

        existing = self._load_insights()
        merged = self._insight_analyzer.merge_insights(existing, new_insights)
        added = len(merged) - len(existing)
        self._save_insights(merged)
        return max(0, added)

    def _sync_to_profile(self, result: CognitionCycleResult) -> None:
        """Copy the freshest awareness/insights into the OnionProfile.

        Reads the current soul layer, attaches the latest windowed notes
        and insights, and writes back. This makes them visible via
        ``profile.recent_awareness`` and ``profile.active_insights`` which
        is what the /api/profile-summary endpoint reads.
        """
        if result.awareness_generated == 0 and result.insight_generated == 0:
            # Nothing to sync, but still update the total counts for observability
            result.total_awareness_after = len(self._load_awareness_notes())
            result.total_insight_after = len(self._load_insights())
            return

        soul_layer = self._memory.get_layer("soul")
        if not soul_layer.data:
            # Profile has not been initialized yet — skip sync silently
            return

        try:
            profile = OnionProfile.from_dict(soul_layer.data)
        except Exception:
            logger.exception("Failed to load OnionProfile during cognition sync")
            return

        all_notes = self._load_awareness_notes()
        all_insights = self._load_insights()

        # Keep the most recent window slice. Order of notes is preserved by
        # the merge functions (append-only with dedup), so taking the tail
        # gives us the newest items.
        profile.recent_awareness = all_notes[-_PROFILE_AWARENESS_WINDOW:]
        profile.active_insights = all_insights[-_PROFILE_INSIGHT_WINDOW:]
        profile.updated_at = datetime.now().isoformat()

        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()

        # Also sync the markdown/json files so the filesystem-visible profile
        # reflects the new awareness/insights.
        try:
            self._memory.sync_profile_files(profile)
        except Exception:
            logger.debug("Failed to sync profile files after cognition cycle", exc_info=True)

        result.total_awareness_after = len(all_notes)
        result.total_insight_after = len(all_insights)

    # -- Memory layer helpers (mirrors SoulEngine's private helpers) ----------

    def _load_awareness_notes(self) -> list[AwarenessNote]:
        layer_data = self._memory.get_layer("awareness").data
        notes = layer_data.get("notes", [])
        return [awareness_note_from_dict(item) for item in notes if isinstance(item, dict)]

    def _save_awareness_notes(self, notes: list[AwarenessNote]) -> None:
        layer = self._memory.get_layer("awareness")
        layer.data.clear()
        layer.data.update(
            {
                "notes": [awareness_note_to_dict(item) for item in notes],
            }
        )
        layer.save()

    def _load_insights(self) -> list[InsightHypothesis]:
        layer_data = self._memory.get_layer("insight").data
        hypotheses = layer_data.get("hypotheses", [])
        return [insight_hypothesis_from_dict(item) for item in hypotheses if isinstance(item, dict)]

    def _save_insights(self, insights: list[InsightHypothesis]) -> None:
        layer = self._memory.get_layer("insight")
        layer.data.clear()
        layer.data.update(
            {
                "hypotheses": [insight_hypothesis_to_dict(item) for item in insights],
            }
        )
        layer.save()

    # -- State persistence ----------------------------------------------------

    def _state_path(self) -> Path | None:
        data_dir = getattr(self._memory, "_data_dir", None)
        if data_dir is None:
            return None
        return Path(data_dir) / "memory" / "cognition_cycle_state.json"

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if path is None or not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.debug("Failed to save cognition cycle state", exc_info=True)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
