"""Offline, code-backed Pydantic Evals invariants for typed AI tasks."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from openbiliclaw.features.profile.domain import ProfileDelta
from openbiliclaw.infrastructure.ai.grounding import is_grounded_in
from openbiliclaw.infrastructure.ai.tasks import (
    CandidateBatchAssessmentInput,
    CandidateBatchAssessmentOutput,
    KeywordGenerationInput,
    KeywordGenerationOutput,
    ProfileDeltaInput,
    RecommendationExplanationInput,
    RecommendationExplanationOutput,
)

EvalData = dict[str, object]


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric: str = Field(min_length=1)


class _ProfileMetadata(_Metadata):
    min_changes: int = Field(ge=1)
    max_changes: int = Field(ge=1)
    required_concepts: tuple[str, ...] = Field(min_length=1)


class _KeywordMetadata(_Metadata):
    min_keywords: int = Field(ge=1)
    max_keywords: int = Field(ge=1)
    required_concepts: tuple[str, ...] = Field(min_length=1)
    minimum_relevant_keywords: int = Field(ge=1)
    forbidden_source_terms: tuple[str, ...] = ()


class _CandidateMetadata(_Metadata):
    score_ranges: dict[str, tuple[float, float]]
    required_topics: tuple[str, ...] = Field(min_length=1)
    minimum_topic_matches: int = Field(ge=1)


class _RecommendationMetadata(_Metadata):
    min_characters: int = Field(ge=1)
    max_characters: int = Field(ge=1)
    required_concepts: tuple[str, ...] = Field(min_length=1)
    minimum_concept_matches: int = Field(ge=1)


class _VersionedEvaluator:
    def get_evaluator_version(self) -> str:
        return "v1"


@dataclass
class ProfileDeltaInvariants(_VersionedEvaluator, Evaluator[EvalData, EvalData, EvalData]):
    """Evaluate evidence provenance, admissible changes, and required concepts."""

    def evaluate(self, ctx: EvaluatorContext[EvalData, EvalData, EvalData]) -> dict[str, bool]:
        task_input = ProfileDeltaInput.model_validate(ctx.inputs)
        output = ProfileDelta.model_validate(ctx.output)
        metadata = _ProfileMetadata.model_validate(ctx.metadata)
        supplied_ids = {evidence.id for evidence in task_input.evidence}
        evidence_valid = all(set(facet.evidence_ids) <= supplied_ids for facet in output.upserts)
        current = {
            (facet.name, facet.value.casefold()): facet for facet in task_input.profile.facets
        }
        removals_valid = all(
            (facet := current.get((name, value.casefold()))) is not None and not facet.overridden
            for name, value in output.removals
        )
        change_count = (
            int(output.narrative is not None) + len(output.upserts) + len(output.removals)
        )
        change_valid = (
            metadata.min_changes <= change_count <= metadata.max_changes
            and not any(facet.overridden for facet in output.upserts)
            and removals_valid
        )
        output_text = " ".join(
            part
            for part in (
                output.narrative or "",
                *(facet.value for facet in output.upserts),
            )
            if part
        )
        return {
            "profile_evidence_valid": evidence_valid,
            "profile_change_valid": change_valid,
            "profile_concepts_present": _count_concepts(output_text, metadata.required_concepts)
            > 0,
        }


@dataclass
class KeywordGenerationInvariants(_VersionedEvaluator, Evaluator[EvalData, EvalData, EvalData]):
    """Evaluate query bounds, uniqueness, profile relevance, and source neutrality."""

    def evaluate(self, ctx: EvaluatorContext[EvalData, EvalData, EvalData]) -> dict[str, bool]:
        task_input = KeywordGenerationInput.model_validate(ctx.inputs)
        output = KeywordGenerationOutput.model_validate(ctx.output)
        metadata = _KeywordMetadata.model_validate(ctx.metadata)
        normalized = [_normalize(keyword) for keyword in output.keywords]
        constraints_valid = (
            metadata.min_keywords <= len(output.keywords) <= metadata.max_keywords
            and len(output.keywords) <= task_input.limit
            and len(set(normalized)) == len(normalized)
            and all(normalized)
        )
        relevant_count = sum(
            _count_concepts(keyword, metadata.required_concepts) > 0 for keyword in output.keywords
        )
        forbidden = tuple(_normalize(term) for term in metadata.forbidden_source_terms)
        return {
            "keyword_constraints_valid": constraints_valid,
            "keyword_relevance_valid": relevant_count >= metadata.minimum_relevant_keywords,
            "keyword_source_neutral": not any(
                term in keyword for keyword in normalized for term in forbidden
            ),
        }


@dataclass
class CandidateBatchAssessmentInvariants(
    _VersionedEvaluator, Evaluator[EvalData, EvalData, EvalData]
):
    """Evaluate batch coverage, case-specific score ranges, and expected topics."""

    def evaluate(self, ctx: EvaluatorContext[EvalData, EvalData, EvalData]) -> dict[str, bool]:
        task_input = CandidateBatchAssessmentInput.model_validate(ctx.inputs)
        output = CandidateBatchAssessmentOutput.model_validate(ctx.output)
        metadata = _CandidateMetadata.model_validate(ctx.metadata)
        expected_ids = {item.id for item in task_input.content}
        actual_ids = [item.content_id for item in output.assessments]
        score_ranges_valid = all(
            set(metadata.score_ranges) == {"relevance", "quality", "novelty", "risk"}
            and all(
                lower <= getattr(item, name) <= upper
                for name, (lower, upper) in metadata.score_ranges.items()
            )
            for item in output.assessments
        )
        topic_text = " ".join(topic for item in output.assessments for topic in item.topics)
        return {
            "candidate_batch_identity_valid": (
                len(actual_ids) == len(set(actual_ids))
                and set(actual_ids) == expected_ids
                and all(
                    item.profile_revision == task_input.profile.revision
                    for item in output.assessments
                )
            ),
            "candidate_batch_score_ranges_valid": score_ranges_valid,
            "candidate_batch_topics_valid": (
                _count_concepts(topic_text, metadata.required_topics)
                >= metadata.minimum_topic_matches
            ),
        }


@dataclass
class RecommendationExplanationInvariants(
    _VersionedEvaluator, Evaluator[EvalData, EvalData, EvalData]
):
    """Evaluate deterministic grounding, length, and case-specific concepts."""

    def evaluate(self, ctx: EvaluatorContext[EvalData, EvalData, EvalData]) -> dict[str, bool]:
        task_input = RecommendationExplanationInput.model_validate(ctx.inputs)
        output = RecommendationExplanationOutput.model_validate(ctx.output)
        metadata = _RecommendationMetadata.model_validate(ctx.metadata)
        facts = (
            task_input.content.title,
            task_input.content.summary,
            task_input.profile.narrative,
            task_input.assessment.explanation,
            *task_input.assessment.topics,
        )
        return {
            "recommendation_grounded": is_grounded_in(facts, output.explanation),
            "recommendation_length_valid": (
                metadata.min_characters
                <= len(output.explanation.strip())
                <= metadata.max_characters
            ),
            "recommendation_concepts_present": (
                _count_concepts(output.explanation, metadata.required_concepts)
                >= metadata.minimum_concept_matches
            ),
        }


TASK_EVALUATOR_TYPES: tuple[type[Evaluator[EvalData, EvalData, EvalData]], ...] = (
    ProfileDeltaInvariants,
    KeywordGenerationInvariants,
    CandidateBatchAssessmentInvariants,
    RecommendationExplanationInvariants,
)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _count_concepts(text: str, concepts: tuple[str, ...]) -> int:
    normalized = _normalize(text)
    return sum(_normalize(concept) in normalized for concept in concepts)
