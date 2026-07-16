"""Shared native model-bundle composition for standalone evaluation scripts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.api.runtime_context import RuntimeModelBundle, build_runtime_model_bundle
from openbiliclaw.llm.concurrency import LLMConcurrencyGate
from openbiliclaw.model_config import compute_model_revision

if TYPE_CHECKING:
    from openbiliclaw.config import Config
    from openbiliclaw.memory.manager import MemoryManager


def build_script_model_bundle(config: Config, memory: MemoryManager) -> RuntimeModelBundle:
    """Build one ordered-route bundle without starting any application runtime."""
    gate = LLMConcurrencyGate(config.models.chat.concurrency)
    return build_runtime_model_bundle(
        config.models,
        compute_model_revision(config.models),
        memory=memory,
        usage_sink=None,
        concurrency_gate=gate,
    )
