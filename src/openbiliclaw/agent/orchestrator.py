"""Agent Orchestrator — the brain that coordinates all modules.

Responsibilities:
- Task scheduling and strategy decisions
- Multi-step reasoning and self-reflection
- Skill registration, discovery, and dispatch
- Coordinating Soul Engine, Discovery Engine, and Recommendation Engine
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.config import Config
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    from .skill import Skill

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Central orchestrator that coordinates all agent components.

    The orchestrator is responsible for:
    1. Managing the agent lifecycle (init → run → shutdown)
    2. Routing tasks to appropriate engines
    3. Maintaining the feedback loop
    4. Self-reflection and strategy adjustment
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._skills: dict[str, Skill] = {}
        self._soul_engine: SoulEngine | None = None
        self._memory_manager: MemoryManager | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize all agent components."""
        logger.info("Initializing Agent Orchestrator...")

        # TODO: Initialize LLM providers
        # TODO: Initialize memory manager
        # TODO: Initialize soul engine
        # TODO: Initialize discovery engine
        # TODO: Initialize recommendation engine
        # TODO: Load built-in skills
        # TODO: Load custom skills

        self._initialized = True
        logger.info("Agent Orchestrator initialized successfully.")

    def register_skill(self, skill: Skill) -> None:
        """Register a skill for the agent to use.

        Args:
            skill: The skill instance to register.
        """
        if skill.name in self._skills:
            logger.warning("Skill '%s' already registered, overwriting.", skill.name)
        self._skills[skill.name] = skill
        logger.info("Registered skill: %s", skill.name)

    def get_skill(self, name: str) -> Skill | None:
        """Get a registered skill by name."""
        return self._skills.get(name)

    @property
    def available_skills(self) -> list[str]:
        """List of registered skill names."""
        return list(self._skills.keys())

    async def run_discovery_cycle(self) -> None:
        """Run a full content discovery cycle.

        This is the main loop that:
        1. Reads the current user soul profile
        2. Generates discovery strategies
        3. Executes discovery via skills
        4. Evaluates and ranks discovered content
        5. Generates friend-style recommendations
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized. Call initialize() first.")

        logger.info("Starting discovery cycle...")
        # TODO: Implement discovery cycle

    async def process_feedback(self, feedback: dict) -> None:  # type: ignore[type-arg]
        """Process user feedback and update all layers.

        Args:
            feedback: Feedback data from the user.
        """
        logger.info("Processing user feedback...")
        # TODO: Route feedback to memory manager and soul engine

    async def chat(self, message: str) -> str:
        """Handle a chat message from the user (Socratic dialogue).

        Args:
            message: User's message.

        Returns:
            Agent's response.
        """
        logger.info("Chat message received: %s", message[:50])
        # TODO: Implement Socratic dialogue
        return "（对话功能开发中...）"

    async def shutdown(self) -> None:
        """Gracefully shut down the agent."""
        logger.info("Shutting down Agent Orchestrator...")
        # TODO: Save state, close connections
        self._initialized = False
