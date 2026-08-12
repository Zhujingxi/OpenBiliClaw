from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.core.extensions import ExtensionKind, ObservationProviderRegistration
from openbiliclaw.observations.producers import ObservationProvider

if TYPE_CHECKING:
    from openbiliclaw.observations.models import Observation


def _imports(node: ast.AST, package: str) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()


def test_observations_import_only_approved_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "observations"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            for module in _imports(node, "openbiliclaw.observations"):
                top = module.split(".")[0]
                allowed = (node.level > 0 if isinstance(node, ast.ImportFrom) else False) or (
                    top in sys.stdlib_module_names
                    or top == "pydantic"
                    or module.startswith("openbiliclaw.observations")
                    or module.startswith("openbiliclaw.core")
                    or module.startswith("openbiliclaw.infrastructure")
                    or module.startswith("openbiliclaw.content.integration")
                )
                if not allowed:
                    violations.append(f"{path.name}:{getattr(node, 'lineno', 0)}:{module}")
    assert violations == []


class StubProvider:
    registration = ObservationProviderRegistration(
        extension_id="example.observations", capability_version=1
    )
    allowed_event_types = frozenset({"content_opened"})

    async def observations(self) -> tuple[Observation, ...]:
        return ()


def test_observation_provider_uses_core_registration() -> None:
    provider: ObservationProvider = StubProvider()
    assert ObservationProvider.__name__ == "ObservationProvider"
    assert provider.registration.kind is ExtensionKind.OBSERVATION_PROVIDER
