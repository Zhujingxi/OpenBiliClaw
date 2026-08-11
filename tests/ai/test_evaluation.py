from __future__ import annotations

from openbiliclaw.ai.evaluation.datasets import Dataset, EvaluationCase
from openbiliclaw.ai.evaluation.reports import Comparison, EvaluationReport, Metric
from openbiliclaw.ai.evaluation.runner import EvaluationRunner
from openbiliclaw.ai.runtime.capabilities import AgentId


async def test_offline_runner_uses_recorded_inputs_and_reports_typed_results() -> None:
    dataset = Dataset(
        "recorded-v1",
        AgentId("test.upper"),
        (EvaluationCase("one", "hello", "HELLO"), EvaluationCase("two", "bye", "BYE")),
    )

    class Executor:
        async def __call__(self, recorded_input: str) -> str:
            return recorded_input.upper()

    runner: EvaluationRunner[str, str] = EvaluationRunner(Executor())
    run = await runner.run(dataset)
    assert [item.output for item in run.results] == ["HELLO", "BYE"]
    assert all(item.passed for item in run.results)

    report = EvaluationReport(run.agent_id, run.dataset_id, (Metric("accuracy", 1.0),))
    comparison = Comparison(report, report)
    assert comparison.delta("accuracy") == 0
    assert comparison.delta("missing") is None


def test_dataset_and_metrics_validate_invariants() -> None:
    import pytest

    with pytest.raises(ValueError):
        Dataset("", AgentId("x"), ())
    with pytest.raises(ValueError):
        Metric("accuracy", 1.1)


def test_dataset_and_report_reject_duplicate_or_mismatched_identifiers() -> None:
    import pytest

    case = EvaluationCase("same", "a", "a")
    with pytest.raises(ValueError, match="case IDs"):
        Dataset("d", AgentId("a"), (case, case))
    with pytest.raises(ValueError, match="case ID"):
        EvaluationCase("", "a", "a")
    metric = Metric("accuracy", 1)
    with pytest.raises(ValueError, match="metric names"):
        EvaluationReport(AgentId("a"), "d", (metric, metric))
    with pytest.raises(ValueError, match="dataset ID"):
        EvaluationReport(AgentId("a"), "", ())
    baseline = EvaluationReport(AgentId("a"), "d", ())
    with pytest.raises(ValueError, match="same agent"):
        Comparison(baseline, EvaluationReport(AgentId("b"), "d", ()))
    with pytest.raises(ValueError, match="same dataset"):
        Comparison(baseline, EvaluationReport(AgentId("a"), "other", ()))
