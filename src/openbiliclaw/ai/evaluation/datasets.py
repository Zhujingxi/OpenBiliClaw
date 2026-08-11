"""Generic immutable offline evaluation datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from openbiliclaw.ai.runtime.capabilities import AgentId

InputT = TypeVar("InputT")
ExpectedT = TypeVar("ExpectedT")


@dataclass(frozen=True, slots=True)
class EvaluationCase(Generic[InputT, ExpectedT]):
    """One identified recorded input and expected output."""

    case_id: str
    input: InputT
    expected: ExpectedT

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("evaluation case ID must not be empty")


@dataclass(frozen=True, slots=True)
class Dataset(Generic[InputT, ExpectedT]):
    """Versioned recorded cases owned by a domain module."""

    dataset_id: str
    agent_id: AgentId
    cases: tuple[EvaluationCase[InputT, ExpectedT], ...]

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise ValueError("dataset ID must not be empty")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation case IDs must be unique")
