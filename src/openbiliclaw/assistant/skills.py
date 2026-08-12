"""Narrow bounded AssistantSkill extension contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai import Tool

from openbiliclaw.core.extensions import AssistantSkillRegistration

from .tools import ApplicationFacade

if TYPE_CHECKING:
    from openbiliclaw.ai.runtime.capabilities import ModelCapabilities, ModelRequirements

ToolFactory = Callable[[ApplicationFacade], tuple[Tool[None], ...]]


@dataclass(frozen=True, slots=True)
class AssistantSkill:
    skill_id: str
    capability_version: int
    requirements: ModelRequirements
    tool_factory: ToolFactory
    static_instructions: str | None = None

    def registration(self) -> AssistantSkillRegistration:
        return AssistantSkillRegistration(self.skill_id, self.capability_version)


class AssistantSkillRegistry:
    """Validate skills and tool names at startup; not a lifecycle hook bus."""

    def __init__(self, model_capabilities: ModelCapabilities) -> None:
        self._capabilities = model_capabilities
        self._skills: dict[str, AssistantSkill] = {}
        self._tool_names: set[str] = set()

    @property
    def skills(self) -> tuple[AssistantSkill, ...]:
        return tuple(self._skills.values())

    def register(self, skill: AssistantSkill, facade: ApplicationFacade | None = None) -> None:
        if skill.skill_id in self._skills:
            raise ValueError(f"duplicate Assistant skill: {skill.skill_id}")
        if not self._capabilities.satisfies(skill.requirements):
            raise ValueError(f"incompatible Assistant skill: {skill.skill_id}")
        tools = (
            skill.tool_factory(facade) if facade is not None else skill.tool_factory(_NullFacade())
        )
        names = tuple(tool.name for tool in tools)
        if len(names) != len(set(names)) or self._tool_names.intersection(names):
            raise ValueError("duplicate tool name across Assistant skills")
        self._skills[skill.skill_id] = skill
        self._tool_names.update(names)


class _NullFacade:
    """Construction-only facade; factories must not call workflows at registration."""

    async def get_recommendations(self, limit: int) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def search_content(self, provider_id: str, text: str, limit: int) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def get_content_details(self, reference: str) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def record_feedback(self, reference: str, kind: str) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def show_profile(self) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def edit_profile(self, claim_id: str, operation: str, value: str | None) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def list_sources(self) -> object:
        raise RuntimeError("skill factory called a workflow during registration")

    async def connect_source(self, provider_id: str) -> object:
        raise RuntimeError("skill factory called a workflow during registration")
