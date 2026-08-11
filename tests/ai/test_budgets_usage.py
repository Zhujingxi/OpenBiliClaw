from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
from pydantic_ai.usage import RunUsage

from openbiliclaw.ai.runtime.budgets import RunPolicy, RunPriority
from openbiliclaw.ai.runtime.capabilities import AgentId
from openbiliclaw.ai.runtime.usage import UsageAttribution, UsageRecord


def test_run_policy_maps_every_pydantic_usage_limit() -> None:
    policy = RunPolicy(
        request_limit=2,
        input_tokens_limit=100,
        output_tokens_limit=50,
        total_tokens_limit=120,
        tool_calls_limit=3,
        timeout_seconds=2,
        retries=1,
        priority=RunPriority.INTERACTIVE,
    )
    limits = policy.to_usage_limits()
    assert limits.request_limit == 2
    assert limits.input_tokens_limit == 100
    assert limits.output_tokens_limit == 50
    assert limits.total_tokens_limit == 120
    assert limits.tool_calls_limit == 3


@pytest.mark.parametrize(
    "build",
    [
        lambda: RunPolicy(request_limit=0),
        lambda: RunPolicy(input_tokens_limit=0),
        lambda: RunPolicy(output_tokens_limit=0),
        lambda: RunPolicy(total_tokens_limit=0),
        lambda: RunPolicy(tool_calls_limit=0),
        lambda: RunPolicy(tool_result_bytes_limit=0),
        lambda: RunPolicy(timeout_seconds=0),
        lambda: RunPolicy(retries=-1),
    ],
)
def test_run_policy_rejects_invalid_limits(build: Callable[[], RunPolicy]) -> None:
    with pytest.raises(ValueError):
        build()


def test_usage_record_attributes_all_dimensions() -> None:
    attribution = UsageAttribution(
        agent_id=AgentId("assistant.reply"),
        workflow="dialogue",
        model_instance="test",
        provider="test",
        recommendation_batch="batch-1",
    )
    record = UsageRecord.from_run_usage(
        attribution, RunUsage(requests=2, input_tokens=3, output_tokens=4, tool_calls=1), 0.5
    )
    assert (record.requests, record.input_tokens, record.output_tokens, record.tool_calls) == (
        2,
        3,
        4,
        1,
    )
    assert record.attribution.recommendation_batch == "batch-1"
    assert record.elapsed_seconds == 0.5


def test_usage_attribution_requires_dimensions() -> None:
    with pytest.raises(ValueError):
        UsageAttribution(AgentId("a"), "", "m", "p")
