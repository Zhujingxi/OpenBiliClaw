"""Explicit frozen application dependency facade; never a service locator."""

from dataclasses import dataclass
from typing import Protocol


class AccessDependency(Protocol):
    """Marker protocol for the explicitly supplied access boundary."""


class WorkflowStateDependency(Protocol):
    """Marker protocol for explicitly supplied workflow persistence."""


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """Small composition value; workflows still receive narrow dependencies."""

    access: AccessDependency
    workflows: WorkflowStateDependency
