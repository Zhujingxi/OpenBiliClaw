from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.runtime.capabilities import AgentId, ModelCapabilities, ModelRequirements
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable


@pytest.mark.parametrize(
    ("field", "required", "advertised", "compatible"),
    [
        ("tools", True, True, True),
        ("tools", True, False, False),
        ("structured_output", True, True, True),
        ("structured_output", True, False, False),
        ("vision", True, True, True),
        ("vision", True, False, False),
        ("streaming", True, True, True),
        ("streaming", True, False, False),
        ("reasoning", True, True, True),
        ("reasoning", True, False, False),
    ],
)
def test_each_boolean_capability_is_mandatory(
    field: str, required: bool, advertised: bool, compatible: bool
) -> None:
    requirements = ModelRequirements(**{field: required})
    capabilities = ModelCapabilities(**{field: advertised})
    assert capabilities.satisfies(requirements) is compatible


def test_context_limit_is_mandatory() -> None:
    assert ModelCapabilities(context_tokens=100).satisfies(ModelRequirements(context_tokens=100))
    assert not ModelCapabilities(context_tokens=99).satisfies(ModelRequirements(context_tokens=100))


def _model(name: str, capabilities: ModelCapabilities) -> ConfiguredModel:
    return ConfiguredModel(name, "test", TestModel(), capabilities)


def test_route_rejects_an_incompatible_fallback_at_startup() -> None:
    required = ModelRequirements(vision=True)
    with pytest.raises(ValueError, match="fallback"):
        ModelRoute(
            AgentId("understanding.image"),
            required,
            (
                _model("primary", ModelCapabilities(vision=True)),
                _model("fallback", ModelCapabilities()),
            ),
        )


def test_route_table_rejects_duplicate_agents_and_requirement_drift() -> None:
    agent_id = AgentId("recommendation.evaluate")
    route = ModelRoute(agent_id, ModelRequirements(), (_model("a", ModelCapabilities()),))
    with pytest.raises(ValueError, match="duplicate"):
        RouteTable((route, route))
    table = RouteTable((route,))
    assert table.resolve(agent_id, ModelRequirements()) is route
    with pytest.raises(ValueError, match="requirements"):
        table.resolve(agent_id, ModelRequirements(tools=True))
    with pytest.raises(KeyError):
        table.resolve(AgentId("unknown.agent"), ModelRequirements())


def test_ids_and_routes_validate_empty_values() -> None:
    with pytest.raises(ValueError):
        AgentId("")
    with pytest.raises(ValueError):
        ConfiguredModel("", "test", TestModel(), ModelCapabilities())
    with pytest.raises(ValueError):
        ModelRoute(AgentId("a"), ModelRequirements(), ())


def test_capability_context_limits_are_non_negative() -> None:
    with pytest.raises(ValueError):
        ModelRequirements(context_tokens=-1)
    with pytest.raises(ValueError):
        ModelCapabilities(context_tokens=-1)
    with pytest.raises(ValueError, match="duplicate model"):
        same = _model("same", ModelCapabilities())
        ModelRoute(AgentId("a"), ModelRequirements(), (same, same))
