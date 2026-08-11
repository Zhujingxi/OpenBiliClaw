"""Explicit, startup-validated agent-to-model routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from openbiliclaw.ai.runtime.capabilities import AgentId, ModelCapabilities, ModelRequirements


@dataclass(frozen=True, slots=True)
class ConfiguredModel:
    """One model instance and its non-secret routing metadata."""

    instance_id: str
    provider: str
    model: Model
    capabilities: ModelCapabilities

    def __post_init__(self) -> None:
        if not self.instance_id.strip() or not self.provider.strip():
            raise ValueError("model instance ID and provider must not be empty")


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Ordered compatible models for exactly one stable agent."""

    agent_id: AgentId
    requirements: ModelRequirements
    models: tuple[ConfiguredModel, ...]

    def __post_init__(self) -> None:
        if not self.models:
            raise ValueError("model route must contain a primary model")
        ids: set[str] = set()
        for index, configured in enumerate(self.models):
            if configured.instance_id in ids:
                raise ValueError(f"duplicate model instance: {configured.instance_id}")
            ids.add(configured.instance_id)
            if not configured.capabilities.satisfies(self.requirements):
                role = "primary" if index == 0 else "fallback"
                raise ValueError(f"{role} model {configured.instance_id} is incompatible")


class RouteTable:
    """Immutable route lookup validated before runtime construction."""

    def __init__(self, routes: tuple[ModelRoute, ...]) -> None:
        by_agent: dict[AgentId, ModelRoute] = {}
        for route in routes:
            if route.agent_id in by_agent:
                raise ValueError(f"duplicate agent route: {route.agent_id}")
            by_agent[route.agent_id] = route
        self._routes = by_agent

    def resolve(self, agent_id: AgentId, requirements: ModelRequirements) -> ModelRoute:
        """Resolve an exact route; runtime requirement drift is rejected."""

        try:
            route = self._routes[agent_id]
        except KeyError:
            raise KeyError(f"no route for agent {agent_id}") from None
        if route.requirements != requirements:
            raise ValueError(f"runtime requirements differ from configured route for {agent_id}")
        return route
