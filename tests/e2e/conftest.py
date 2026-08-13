"""Explicitly enable real PydanticAI model calls for E2E tests."""

import pytest
from pydantic_ai import models


@pytest.fixture(autouse=True, scope="session")
def enable_real_model_requests() -> None:
    models.ALLOW_MODEL_REQUESTS = True
