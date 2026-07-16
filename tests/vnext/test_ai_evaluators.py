"""Offline semantic-evaluation contracts for the four typed AI tasks."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import models
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import LLMJudge

from openbiliclaw.infrastructure.ai.evaluators import (
    CandidateAssessmentInvariants,
    KeywordGenerationInvariants,
    ProfileDeltaInvariants,
    RecommendationExplanationInvariants,
)
from openbiliclaw.infrastructure.ai.grounding import is_grounded_in

REPOSITORY_ROOT = Path(__file__).parents[2]
models.ALLOW_MODEL_REQUESTS = False
EVALUATOR_TYPES = (
    ProfileDeltaInvariants,
    KeywordGenerationInvariants,
    CandidateAssessmentInvariants,
    RecommendationExplanationInvariants,
)


def _load_dataset(name: str) -> Dataset[dict[str, object], dict[str, object], dict[str, object]]:
    return Dataset[dict[str, object], dict[str, object], dict[str, object]].from_file(
        REPOSITORY_ROOT / "evals" / "datasets" / f"{name}.yaml",
        custom_evaluator_types=EVALUATOR_TYPES,
    )


async def _offline_assertions(name: str, output: dict[str, object]) -> dict[str, bool]:
    dataset = _load_dataset(name)
    offline = [evaluator for evaluator in dataset.evaluators if not isinstance(evaluator, LLMJudge)]
    assert len(offline) == 1
    assert offline[0].get_evaluator_version() == "v1"
    report = await Dataset(
        name=f"{name}_offline",
        cases=[dataset.cases[0]],
        evaluators=offline,
    ).evaluate(lambda inputs: output, progress=False)
    return {
        assertion_name: bool(result.value)
        for assertion_name, result in report.cases[0].assertions.items()
    }


@pytest.mark.parametrize(
    ("dataset_name", "alternative_output", "expected_assertions"),
    [
        (
            "profile_delta",
            {
                "narrative": "Practical creative workflows now include procedural modeling.",
                "upserts": [
                    {
                        "name": "interests",
                        "value": "procedural modeling workflows",
                        "weight": 0.7,
                        "confidence": 0.75,
                        "evidence_ids": ["00000000-0000-0000-0000-000000000101"],
                        "overridden": False,
                    }
                ],
                "removals": [],
            },
            {"profile_evidence_valid", "profile_change_valid", "profile_concepts_present"},
        ),
        (
            "keyword_generation",
            {
                "keywords": [
                    "procedural 3D workflows",
                    "geometry node exercises",
                    "modeling without destructive edits",
                ]
            },
            {"keyword_constraints_valid", "keyword_relevance_valid", "keyword_source_neutral"},
        ),
        (
            "candidate_assessment",
            {
                "content_id": "00000000-0000-0000-0000-000000000201",
                "profile_revision": 1,
                "relevance": 0.82,
                "quality": 0.76,
                "novelty": 0.55,
                "risk": 0.08,
                "topics": ["geometry nodes", "3D workflow"],
                "explanation": "A practical project aligned with the supplied profile.",
            },
            {"candidate_identity_valid", "candidate_score_ranges_valid", "candidate_topics_valid"},
        ),
        (
            "recommendation_explanation",
            {
                "explanation": (
                    "A hands-on Geometry Nodes project that suits your practical "
                    "procedural-modeling interests."
                )
            },
            {
                "recommendation_grounded",
                "recommendation_length_valid",
                "recommendation_concepts_present",
            },
        ),
    ],
)
async def test_offline_invariants_accept_semantically_valid_alternatives(
    dataset_name: str,
    alternative_output: dict[str, object],
    expected_assertions: set[str],
) -> None:
    dataset = _load_dataset(dataset_name)
    assert alternative_output != dataset.cases[0].expected_output
    assertions = await _offline_assertions(dataset_name, alternative_output)

    assert set(assertions) == expected_assertions
    assert all(assertions.values())


@pytest.mark.parametrize(
    ("dataset_name", "invalid_output", "failed_assertion"),
    [
        (
            "profile_delta",
            {
                "narrative": "Adds procedural modeling.",
                "upserts": [
                    {
                        "name": "interests",
                        "value": "procedural modeling",
                        "weight": 0.8,
                        "confidence": 0.8,
                        "evidence_ids": ["00000000-0000-0000-0000-000000009999"],
                        "overridden": False,
                    }
                ],
                "removals": [],
            },
            "profile_evidence_valid",
        ),
        (
            "profile_delta",
            {
                "narrative": "Adds procedural modeling.",
                "upserts": [
                    {
                        "name": "interests",
                        "value": "procedural modeling",
                        "weight": 0.8,
                        "confidence": 1.0,
                        "evidence_ids": ["00000000-0000-0000-0000-000000000101"],
                        "overridden": True,
                    }
                ],
                "removals": [],
            },
            "profile_change_valid",
        ),
        (
            "profile_delta",
            {
                "narrative": "Enjoys unrelated cooking videos.",
                "upserts": [],
                "removals": [],
            },
            "profile_concepts_present",
        ),
        (
            "keyword_generation",
            {"keywords": ["procedural modeling", "Procedural Modeling"]},
            "keyword_constraints_valid",
        ),
        (
            "keyword_generation",
            {"keywords": ["bread recipes", "urban gardening"]},
            "keyword_relevance_valid",
        ),
        (
            "keyword_generation",
            {"keywords": ["bilibili procedural modeling", "geometry workflow"]},
            "keyword_source_neutral",
        ),
        (
            "candidate_assessment",
            {
                "content_id": "00000000-0000-0000-0000-000000009999",
                "profile_revision": 1,
                "relevance": 0.8,
                "quality": 0.8,
                "novelty": 0.6,
                "risk": 0.1,
                "topics": ["geometry nodes"],
                "explanation": "Grounded.",
            },
            "candidate_identity_valid",
        ),
        (
            "candidate_assessment",
            {
                "content_id": "00000000-0000-0000-0000-000000000201",
                "profile_revision": 1,
                "relevance": 0.2,
                "quality": 0.8,
                "novelty": 0.6,
                "risk": 0.1,
                "topics": ["geometry nodes"],
                "explanation": "Grounded.",
            },
            "candidate_score_ranges_valid",
        ),
        (
            "candidate_assessment",
            {
                "content_id": "00000000-0000-0000-0000-000000000201",
                "profile_revision": 1,
                "relevance": 0.8,
                "quality": 0.8,
                "novelty": 0.6,
                "risk": 0.1,
                "topics": ["bread baking"],
                "explanation": "Grounded.",
            },
            "candidate_topics_valid",
        ),
        (
            "recommendation_explanation",
            {"explanation": "This cooking lesson has excellent bread recipes."},
            "recommendation_grounded",
        ),
        (
            "recommendation_explanation",
            {"explanation": "Geometry Nodes."},
            "recommendation_length_valid",
        ),
        (
            "recommendation_explanation",
            {"explanation": "The supplied walkthrough matches your workflow interests very well."},
            "recommendation_concepts_present",
        ),
    ],
)
async def test_each_offline_invariant_rejects_its_targeted_failure(
    dataset_name: str,
    invalid_output: dict[str, object],
    failed_assertion: str,
) -> None:
    assertions = await _offline_assertions(dataset_name, invalid_output)

    assert assertions[failed_assertion] is False


def test_recommendation_dataset_configures_but_does_not_run_subjective_judge() -> None:
    dataset = _load_dataset("recommendation_explanation")
    judges = [evaluator for evaluator in dataset.evaluators if isinstance(evaluator, LLMJudge)]

    assert len(judges) == 1
    judge = judges[0]
    assert judge.model == "openai:obc-analysis"
    assert judge.include_input is True
    assert judge.include_expected_output is False
    assert "grounded" in judge.rubric.casefold()
    assert "clear" in judge.rubric.casefold()
    assert "invent" in judge.rubric.casefold()


def test_generic_chinese_recommendation_ngrams_do_not_count_as_grounding() -> None:
    assert not is_grounded_in(
        ["这个视频教程很实用"],
        "这个视频很符合你的兴趣。",
    )


def test_one_meaningful_latin_unit_remains_enough_for_short_facts() -> None:
    assert is_grounded_in(["Geometry"], "A Geometry guide.")


def _chinese_recommendation_input() -> dict[str, object]:
    content_id = "00000000-0000-0000-0000-000000000301"
    return {
        "profile": {
            "revision": 3,
            "narrative": "喜欢简洁实用的三维建模教程",
            "facets": [],
            "confidence": 0.9,
            "created_at": "2026-07-17T00:00:00Z",
        },
        "content": {
            "id": content_id,
            "source_id": "bilibili",
            "external_id": "BV1chinese",
            "url": "https://www.bilibili.com/video/BV1chinese",
            "title": "几何节点实战教程",
            "summary": "从案例出发演示节点建模方法。",
            "media_type": "video",
            "metadata": {},
        },
        "assessment": {
            "id": "00000000-0000-0000-0000-000000000302",
            "content_id": content_id,
            "profile_revision": 3,
            "relevance": 0.9,
            "quality": 0.8,
            "novelty": 0.6,
            "risk": 0.1,
            "topics": ["程序化建模"],
            "explanation": "内容与建模兴趣相关。",
        },
    }


@pytest.mark.parametrize(
    ("explanation", "expected_grounded"),
    [
        ("这个视频很符合你对实用建模教程的兴趣。", True),
        ("这个视频详细讲解量子物理和烹饪技巧，也提到建模。", False),
    ],
)
async def test_offline_recommendation_grounding_uses_meaningful_chinese_coverage(
    explanation: str,
    expected_grounded: bool,
) -> None:
    dataset = Dataset(
        name="chinese_recommendation_grounding_v1",
        cases=[
            Case(
                name="reviewed_chinese_facts",
                inputs=_chinese_recommendation_input(),
                expected_output={
                    "explanation": "这个视频很符合你对实用建模教程的兴趣。"
                },
                metadata={
                    "rubric": "解释必须基于输入事实，长度适中，并提及建模。",
                    "min_characters": 10,
                    "max_characters": 100,
                    "required_concepts": ["建模"],
                    "minimum_concept_matches": 1,
                },
            )
        ],
        evaluators=[RecommendationExplanationInvariants()],
    )
    report = await dataset.evaluate(
        lambda inputs: {"explanation": explanation},
        progress=False,
    )

    assertions = report.cases[0].assertions
    assert assertions["recommendation_grounded"].value is expected_grounded
    assert assertions["recommendation_length_valid"].value is True
    assert assertions["recommendation_concepts_present"].value is True
