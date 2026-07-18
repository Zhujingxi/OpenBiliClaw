"""Skill system — extensible capability framework.

Skills are self-contained modules that give the agent specific capabilities.
Users and the community can create custom skills to extend the agent.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Metadata describing a skill."""

    name: str
    description: str
    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)


class Skill(ABC):
    """Base class for all skills.

    A Skill is an independent, self-contained capability that the agent can use.
    Each skill has:
    - A name and description
    - An execute method that performs the skill's action
    - Input/output schema definitions

    To create a custom skill:
    1. Subclass Skill
    2. Implement the `execute` method
    3. Define `metadata` property
    4. Place in the skills/ directory with a SKILL.md file
    """

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return metadata about this skill."""
        ...

    @property
    def name(self) -> str:
        """Skill name shortcut."""
        return self.metadata.name

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Execute the skill.

        Args:
            **kwargs: Skill-specific parameters.

        Returns:
            Skill-specific result.
        """
        ...

    def describe(self) -> str:
        """Return a human-readable description for LLM context."""
        meta = self.metadata
        return f"[{meta.name}] {meta.description}"


class SkillRegistry:
    """Registry for discovering and managing skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill instance."""
        self._skills[skill.name] = skill
        logger.info("Skill registered: %s", skill.name)

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    @property
    def all_skills(self) -> list[Skill]:
        """All registered skills."""
        return list(self._skills.values())

    def describe_all(self) -> str:
        """Return descriptions of all skills (for LLM context)."""
        return "\n".join(skill.describe() for skill in self._skills.values())

    @staticmethod
    def discover_skills(skills_dir: Path) -> list[Path]:
        """Discover skill directories under the given path.

        A valid skill directory contains a SKILL.md file.

        Args:
            skills_dir: Root directory to search for skills.

        Returns:
            List of paths to SKILL.md files.
        """
        if not skills_dir.exists():
            return []
        return sorted(skills_dir.glob("*/SKILL.md"))
