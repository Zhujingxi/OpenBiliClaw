from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from openbiliclaw.assistant.observations import DialogueObservationKind, dialogue_observation

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def test_dialogue_observation_filter_emits_only_explicit_evidence() -> None:
    assert (
        dialogue_observation(DialogueObservationKind.NORMAL_MESSAGE, "hello", "conv", NOW) is None
    )
    for kind in (
        DialogueObservationKind.EXPLICIT_PREFERENCE,
        DialogueObservationKind.EXPLICIT_FEEDBACK,
        DialogueObservationKind.CONFIRMED_EDIT,
        DialogueObservationKind.DEFINED_OUTCOME,
    ):
        assert dialogue_observation(kind, "I prefer science", "conv", NOW) is not None


def test_assistant_imports_only_approved_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "assistant"
    forbidden = (
        "openbiliclaw.infrastructure.credentials",
        "openbiliclaw.storage",
        "openbiliclaw.memory",
        "openbiliclaw.soul",
        "openbiliclaw.recommendation.repositories",
        "openbiliclaw.understanding.repository",
        "openbiliclaw.observations.repository",
    )
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            for module in modules:
                if module.startswith(forbidden):
                    violations.append(f"{path.name}:{getattr(node, 'lineno', 0)}:{module}")
    assert not violations
