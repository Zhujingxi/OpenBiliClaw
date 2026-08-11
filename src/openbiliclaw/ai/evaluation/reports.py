"""Typed, side-effect-free evaluation reports and comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.ai.runtime.capabilities import AgentId


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    value: float

    def __post_init__(self) -> None:
        if not self.name or not 0 <= self.value <= 1:
            raise ValueError("metric requires a name and value in [0, 1]")


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    agent_id: AgentId
    dataset_id: str
    metrics: tuple[Metric, ...]

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise ValueError("dataset ID must not be empty")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("metric names must be unique")


@dataclass(frozen=True, slots=True)
class Comparison:
    baseline: EvaluationReport
    candidate: EvaluationReport

    def __post_init__(self) -> None:
        if self.baseline.agent_id != self.candidate.agent_id:
            raise ValueError("reports must evaluate the same agent")
        if self.baseline.dataset_id != self.candidate.dataset_id:
            raise ValueError("reports must use the same dataset")

    def delta(self, metric_name: str) -> float | None:
        baseline = {metric.name: metric.value for metric in self.baseline.metrics}
        candidate = {metric.name: metric.value for metric in self.candidate.metrics}
        if metric_name not in baseline or metric_name not in candidate:
            return None
        return candidate[metric_name] - baseline[metric_name]
