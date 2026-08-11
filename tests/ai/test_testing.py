from pydantic_ai import models

from openbiliclaw.ai.runtime.testing import FunctionModel, TestModel


def test_offline_fixtures_disable_real_requests() -> None:
    assert models.ALLOW_MODEL_REQUESTS is False
    assert TestModel is not None
    assert FunctionModel is not None
