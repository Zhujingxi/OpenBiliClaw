"""Deterministic PydanticAI models for offline domain-agent tests."""

from pydantic_ai import models
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

# Importing test fixtures makes the safe default explicit. Real-provider tests
# must opt in and carry the integration marker.
models.ALLOW_MODEL_REQUESTS = False

__all__ = ["FunctionModel", "TestModel"]
