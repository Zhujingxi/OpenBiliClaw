"""User Soul Engine — the heart of OpenBiliClaw.

Transforms raw behavioral data into deep, layered understanding of a person.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMProvider
    from openbiliclaw.memory.manager import MemoryManager

from .preference_analyzer import PreferenceAnalyzer
from .profile import SoulProfile, preference_layer_from_dict
from .profile_builder import ProfileBuilder

logger = logging.getLogger(__name__)


class SoulProfileNotInitializedError(Exception):
    """Raised when the soul layer has not been initialized yet."""


class SoulEngine:
    """Engine for building and maintaining deep user understanding.

    The Soul Engine orchestrates the transformation of raw behavioral data
    through the five-layer memory architecture:
      Event → Preference → Awareness → Insight → Soul

    It is responsible for:
    1. Analyzing new behavioral events
    2. Updating preference patterns
    3. Writing daily awareness notes
    4. Generating insight hypotheses
    5. Maintaining the soul-level personality portrait
    """

    def __init__(self, llm: LLMProvider, memory: MemoryManager) -> None:
        self._llm = llm
        self._memory = memory
        self._preference_analyzer = PreferenceAnalyzer(llm)
        self._profile_builder = ProfileBuilder(llm)

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        """Analyze new behavioral events and update all memory layers.

        This is the primary entry point for processing new user behavior.
        Events flow upward through the memory layers, with each layer
        potentially triggering updates in the layers above.

        Args:
            events: List of behavioral event dicts from the collector.
        """
        logger.info("Analyzing %d new events...", len(events))
        preference_layer = self._memory.get_layer("preference")
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=preference_layer.data,
        )
        preference_layer.data.clear()
        preference_layer.data.update(updated_preference)
        preference_layer.save()

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> SoulProfile:
        """Build an initial soul profile from historical data.

        Used on first run to bootstrap the user understanding model
        from existing Bilibili watch history, favorites, etc.

        Args:
            history: Historical data from Bilibili API.

        Returns:
            Initial SoulProfile.
        """
        logger.info("Building initial soul profile from %d history items...", len(history))
        preference_layer = self._memory.get_layer("preference").data
        profile = await self._profile_builder.build(
            history=history,
            preference=preference_layer,
        )
        profile.preferences = preference_layer_from_dict(preference_layer)
        soul_layer = self._memory.get_layer("soul")
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        return profile

    async def get_profile(self) -> SoulProfile:
        """Get the current soul profile.

        Returns:
            Current SoulProfile from the soul memory layer.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        return SoulProfile.from_dict(soul_data)

    async def update_from_feedback(self, feedback: dict[str, Any]) -> None:
        """Update soul understanding based on explicit user feedback.

        This can trigger updates across all memory layers, depending
        on the significance of the feedback.

        Args:
            feedback: User feedback data.
        """
        logger.info("Updating soul from feedback...")
        # TODO: Process feedback through all layers

    async def generate_awareness_note(self) -> str:
        """Generate a daily awareness note.

        The awareness note captures what the agent has observed about
        the user's recent behavior patterns, mood changes, and interest shifts.

        Returns:
            Natural language awareness note.
        """
        # TODO: Generate based on recent events and pattern analysis
        return ""

    async def generate_insight(self) -> str:
        """Generate or update insight hypotheses.

        Insights are deeper interpretations of user behavior:
        - Why they do what they do
        - What psychological needs are being met
        - What latent interests might exist

        Returns:
            Natural language insight.
        """
        # TODO: Generate psychological insights from behavior patterns
        return ""
