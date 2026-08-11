"""Deterministic execution mechanics for recorded offline datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

if TYPE_CHECKING:
    from openbiliclaw.ai.evaluation.datasets import Dataset
    from openbiliclaw.ai.runtime.capabilities import AgentId

InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)
RunInputT = TypeVar("RunInputT")
RunOutputT = TypeVar("RunOutputT")


class EvaluationExecutor(Protocol[InputT, OutputT]):
    """Isolated callable supplied by the evaluated domain."""

    async def __call__(self, recorded_input: InputT) -> OutputT: ...


@dataclass(frozen=True, slots=True)
class EvaluationResult(Generic[RunOutputT]):
    case_id: str
    output: RunOutputT
    passed: bool


@dataclass(frozen=True, slots=True)
class EvaluationRun(Generic[RunOutputT]):
    agent_id: AgentId
    dataset_id: str
    results: tuple[EvaluationResult[RunOutputT], ...]


class EvaluationRunner(Generic[RunInputT, RunOutputT]):
    """Run only recorded inputs through an explicitly supplied executor."""

    def __init__(self, executor: EvaluationExecutor[RunInputT, RunOutputT]) -> None:
        self._executor = executor

    async def run(self, dataset: Dataset[RunInputT, RunOutputT]) -> EvaluationRun[RunOutputT]:
        results: list[EvaluationResult[RunOutputT]] = []
        for case in dataset.cases:
            output = await self._executor(case.input)
            results.append(EvaluationResult(case.case_id, output, output == case.expected))
        return EvaluationRun(dataset.agent_id, dataset.dataset_id, tuple(results))
