"""Replay discovery candidates through two evaluation prompt/input arms.

Usage:
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b compact --sample 100 --repeats 3 \
        --output data/eval/profile-diet-compact.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b reason-diet --sample 100 --repeats 3 \
        --output data/eval/reason-diet.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b body-cap --platform twitter --sample 100 --repeats 3 \
        --output data/eval/profile-diet-body-cap.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b model=<instance-id> --sample 100 --repeats 3 \
        --output data/eval/model-route.json

For ``--arm-b compact``, arm A forces the legacy full-profile/no-recall prompt
shape and arm B uses current production inputs: compact profile plus per-item
``related_interests`` recall when an embedding service is configured. For
``reason-diet`` and ``model=...`` arms, both sides use production profile/recall
shape so the requested arm remains the only intentional difference. The gate
uses the production 4096-token output ceiling and fails on missing evaluation
responses instead of treating gateway/parse failures as genuine zero scores.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.profile_diet_ab")

FLIP_RATE_MAX = 0.03
SPEARMAN_MIN = 0.95
CHUNK_TIMEOUT_SECONDS = 900.0
RATE_LIMIT_RETRY_DELAYS_SECONDS = (65.0, 130.0)

BODY_CAP_HEAD = 200
BODY_CAP_TAIL = 100
BODY_CAP_JOINER = "\u2026"

_REPLAY_STATUSES = frozenset({"evaluated", "cached", "rejected_low_score"})
_DEFAULT_BATCH_SIZE = 30
_RELATIVE_ADMISSION_SHRINK_MAX = 0.03
_REPLAY_SOURCE_CONTEXT = "mixed"
_REPLAY_EVALUATION_OUTPUT_FIELDS = (
    "relevance_score",
    "relevance_reason",
    "topic_key",
    "topic_group",
    "style_key",
    "franchise_key",
    "pool_expression",
    "pool_topic_label",
)
_NON_RETRYABLE_PROVIDER_LIMIT_MARKERS = (
    "http 402",
    "payment required",
    "insufficient balance",
    "insufficient_quota",
    "billing",
    "out of credit",
    "credit exhausted",
    "余额不足",
    "账户余额",
)
_EMPTY_REPLAY_ATTRIBUTION: Mapping[str, object] = {
    "pair_kind": "",
    "repeat": 0,
    "logical_run": "",
    "arm": "",
}
_REPLAY_ATTRIBUTION: ContextVar[Mapping[str, object]] = ContextVar(
    "openbiliclaw_profile_diet_replay_attribution",
    default=_EMPTY_REPLAY_ATTRIBUTION,
)


def _exception_chain_messages(exc: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    return " ".join(messages)


def _is_retryable_replay_rate_limit(exc: BaseException) -> bool:
    """Return whether one failed replay call is safe to retry after cooldown."""

    from openbiliclaw.llm.service import is_llm_rate_limit_error

    messages = _exception_chain_messages(exc)
    if any(marker in messages for marker in _NON_RETRYABLE_PROVIDER_LIMIT_MARKERS):
        return False
    return is_llm_rate_limit_error(exc)


@dataclass(frozen=True)
class ReplayCandidate:
    """Prompt-replay metadata aligned with one candidate score."""

    candidate_id: int
    title: str
    source_strategy: str
    source_platform: str = ""
    content_id: str = ""
    content_url: str = ""
    body_text: str = ""
    score_threshold: float = 0.0


@dataclass(frozen=True)
class ScoreDeltaSummary:
    """Aggregate absolute score-delta metrics."""

    mean_abs_delta: float
    p95_abs_delta: float


@dataclass(frozen=True)
class AdmissionFlipSummary:
    """Admission-threshold flip metrics."""

    flip_count: int
    item_count: int
    flip_rate: float
    per_strategy: dict[str, int]


@dataclass(frozen=True)
class ModelOverride:
    """Parsed model arm override (instance ID in v2, provider:model in legacy)."""

    provider: str
    model: str


@dataclass(frozen=True)
class ReplayMetrics:
    """Metrics for one aligned pair of replay scores."""

    mean_abs_delta: float
    p95_abs_delta: float
    spearman: float
    flip_rate: float
    flip_count: int
    admitted_a: int
    admitted_b: int
    admission_rate_delta: float


@dataclass(frozen=True)
class ReplayPair:
    """One control or treatment replay pair."""

    repeat: int
    kind: str
    first_arm: str
    scores_a: tuple[float, ...]
    scores_b: tuple[float, ...]
    metrics: ReplayMetrics


@dataclass(frozen=True)
class ReplayProfileSnapshot:
    """Frozen raw/effective profile identities used by every replay arm."""

    raw_profile: object
    effective_profile: object
    raw_digest: str
    effective_digest: str
    overrides_present: bool
    active_speculation_count: int


class ReplayEmbeddingValidationError(RuntimeError):
    """Raised when one replay embedding result is not usable evidence."""


class ReplayEmbeddingAudit:
    """Fail-closed wrapper and privacy-safe audit for replay embeddings."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.namespace = _embedding_namespace(inner)
        self.calls: list[dict[str, object]] = []
        self.errors: list[str] = []
        self._dimension: int | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def embed(self, text: str) -> list[float]:
        attribution = dict(_REPLAY_ATTRIBUTION.get())
        request: dict[str, object] = {
            **attribution,
            "request_digest": _digest(str(text)),
            "namespace": self.namespace,
        }
        try:
            raw_vector = await self._inner.embed(text)
            vector = _validated_embedding_vector(raw_vector)
            dimension = len(vector)
            if self._dimension is None:
                self._dimension = dimension
            elif dimension != self._dimension:
                raise ReplayEmbeddingValidationError(
                    f"embedding dimension drift: expected {self._dimension}, got {dimension}"
                )
        except Exception as exc:
            reason = _embedding_error_reason(exc)
            self.errors.append(reason)
            self.calls.append({**request, "status": "error", "dimension": 0, "error": reason})
            if isinstance(exc, ReplayEmbeddingValidationError):
                raise
            raise ReplayEmbeddingValidationError(reason) from exc
        self.calls.append({**request, "status": "ok", "dimension": dimension})
        return vector

    def summary(
        self,
        *,
        eligible_tail_count: int,
        recall_audit: ReplayRecallAudit,
        expected_runs: set[tuple[str, int, str]] | None = None,
    ) -> dict[str, object]:
        blocking_reasons = list(dict.fromkeys(self.errors))
        production_recall_batches = recall_audit.production_batch_count
        if eligible_tail_count > 0 and production_recall_batches <= 0:
            blocking_reasons.append(
                "eligible tail interests existed but production recall was never invoked"
            )
        if eligible_tail_count > 0 and not self.calls:
            blocking_reasons.append(
                "eligible tail interests existed but no embedding request was audited"
            )
        if eligible_tail_count > 0 and expected_runs:
            observed_runs = {
                (
                    str(call.get("pair_kind") or ""),
                    _to_int(call.get("repeat")),
                    str(call.get("logical_run") or ""),
                )
                for call in self.calls
            }
            for missing in sorted(expected_runs - observed_runs):
                blocking_reasons.append(
                    f"production recall run {missing!r} emitted no embedding request"
                )
        return {
            "passed": not blocking_reasons,
            "degraded": False,
            "namespace": self.namespace,
            "call_count": len(self.calls),
            "successful_call_count": sum(call.get("status") == "ok" for call in self.calls),
            "dimension": self._dimension or 0,
            "eligible_tail_count": eligible_tail_count,
            "blocking_reasons": blocking_reasons,
            "calls": [dict(call) for call in self.calls],
        }


class ReplayRecallAudit:
    """Record whether production recall ran and injected labels per logical run."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    @property
    def production_batch_count(self) -> int:
        return sum(event.get("scope") == "batch" for event in self.events)

    def record_batch(
        self,
        result: Mapping[int, Sequence[object]],
        *,
        candidate_count: int,
        complete_candidate_count: int | None = None,
    ) -> None:
        injected = sum(len(labels) for labels in result.values())
        completed = (
            candidate_count if complete_candidate_count is None else complete_candidate_count
        )
        self.events.append(
            {
                **dict(_REPLAY_ATTRIBUTION.get()),
                "scope": "batch",
                "candidate_count": candidate_count,
                "complete_candidate_count": completed,
                "candidates_with_injection": len(result),
                "injected_label_count": injected,
            }
        )

    def record_single(self, result: Sequence[object], *, complete: bool = True) -> None:
        self.events.append(
            {
                **dict(_REPLAY_ATTRIBUTION.get()),
                "scope": "single",
                "candidate_count": 1,
                "complete_candidate_count": int(complete),
                "candidates_with_injection": int(bool(result)),
                "injected_label_count": len(result),
            }
        )

    def payload(self) -> dict[str, object]:
        return {
            "production_batch_count": self.production_batch_count,
            "injected_label_count": sum(
                int(event["injected_label_count"]) for event in self.events
            ),
            "candidates_with_injection": sum(
                int(event["candidates_with_injection"]) for event in self.events
            ),
            "candidate_count": sum(int(event["candidate_count"]) for event in self.events),
            "complete_candidate_count": sum(
                int(event["complete_candidate_count"]) for event in self.events
            ),
            "events": [dict(event) for event in self.events],
        }

    def validate(
        self,
        *,
        expected_runs: set[tuple[str, int, str]],
        minimum_batches_per_run: int,
        expected_candidate_count: int,
    ) -> dict[str, object]:
        grouped: Counter[tuple[str, int, str]] = Counter()
        candidate_counts: Counter[tuple[str, int, str]] = Counter()
        blocking_reasons: list[str] = []
        for event in self.events:
            if event.get("scope") != "batch":
                continue
            key = (
                str(event.get("pair_kind") or ""),
                _to_int(event.get("repeat")),
                str(event.get("logical_run") or ""),
            )
            grouped[key] += 1
            candidate_counts[key] += int(event.get("candidate_count") or 0)
            if int(event.get("complete_candidate_count") or 0) != int(
                event.get("candidate_count") or 0
            ):
                blocking_reasons.append(f"production recall batch {key!r} was incomplete")
        for expected in sorted(expected_runs):
            if grouped[expected] < minimum_batches_per_run:
                blocking_reasons.append(
                    f"production recall run {expected!r} emitted "
                    f"{grouped[expected]} batch audit(s), expected at least "
                    f"{minimum_batches_per_run}"
                )
            if candidate_counts[expected] < expected_candidate_count:
                blocking_reasons.append(
                    f"production recall run {expected!r} covered "
                    f"{candidate_counts[expected]} candidate(s), expected at least "
                    f"{expected_candidate_count}"
                )
        reasons = list(dict.fromkeys(blocking_reasons))
        return {
            **self.payload(),
            "passed": not reasons,
            "blocking_reasons": reasons,
        }


def score_delta_summary(scores_a: Sequence[float], scores_b: Sequence[float]) -> ScoreDeltaSummary:
    """Return mean and nearest-rank p95 absolute score deltas."""

    deltas = _absolute_deltas(scores_a, scores_b)
    if not deltas:
        return ScoreDeltaSummary(mean_abs_delta=0.0, p95_abs_delta=0.0)
    sorted_deltas = sorted(deltas)
    p95_index = min(len(sorted_deltas) - 1, max(0, math.ceil(len(sorted_deltas) * 0.95) - 1))
    return ScoreDeltaSummary(
        mean_abs_delta=sum(sorted_deltas) / len(sorted_deltas),
        p95_abs_delta=sorted_deltas[p95_index],
    )


def spearman_rank_correlation(scores_a: Sequence[float], scores_b: Sequence[float]) -> float:
    """Return Spearman rank correlation for two aligned score lists.

    Ties receive average ranks. If both rank vectors are constant and equal,
    treat the ordering as unchanged and return ``1.0``.
    """

    _validate_aligned_scores(scores_a, scores_b)
    if not scores_a:
        return 1.0

    ranks_a = _average_ranks(scores_a)
    ranks_b = _average_ranks(scores_b)
    mean_a = sum(ranks_a) / len(ranks_a)
    mean_b = sum(ranks_b) / len(ranks_b)
    diffs_a = [rank - mean_a for rank in ranks_a]
    diffs_b = [rank - mean_b for rank in ranks_b]
    numerator = sum(left * right for left, right in zip(diffs_a, diffs_b, strict=True))
    denom_a = sum(value * value for value in diffs_a)
    denom_b = sum(value * value for value in diffs_b)
    denominator = math.sqrt(denom_a * denom_b)
    if denominator == 0:
        return 1.0 if ranks_a == ranks_b else 0.0
    return numerator / denominator


def admission_flip_summary(
    candidates: Sequence[object],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    *,
    admission_min_score: float = 0.60,
) -> AdmissionFlipSummary:
    """Return admission flips using the same effective thresholds as production."""

    _validate_aligned_scores(scores_a, scores_b)
    if len(candidates) != len(scores_a):
        raise ValueError("candidates and score lists must have the same length")

    per_strategy: dict[str, int] = {}
    flip_count = 0
    for candidate, score_a, score_b in zip(candidates, scores_a, scores_b, strict=True):
        strategy = _candidate_strategy(candidate)
        threshold = _candidate_admission_threshold(
            candidate,
            admission_min_score=admission_min_score,
        )
        flipped = score_a >= threshold > score_b or score_b >= threshold > score_a
        if not flipped:
            continue
        flip_count += 1
        per_strategy[strategy] = per_strategy.get(strategy, 0) + 1

    item_count = len(candidates)
    return AdmissionFlipSummary(
        flip_count=flip_count,
        item_count=item_count,
        flip_rate=(flip_count / item_count) if item_count else 0.0,
        per_strategy=dict(sorted(per_strategy.items())),
    )


def select_replay_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    sample: int,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    """Filter and deterministically order recent replay candidate rows."""

    sample_count = max(0, int(sample))
    if sample_count <= 0:
        return []
    platform_filter = _normalize_platform(platform) if platform else ""
    selected: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if status not in _REPLAY_STATUSES:
            continue
        if platform_filter and _normalize_platform(row.get("source_platform")) != platform_filter:
            continue
        selected.append(dict(row))
    selected.sort(key=_candidate_row_sort_key, reverse=True)

    # Preserve the observed production mix. Artificially round-robin sampling
    # platform/strategy groups changes their weights and can make the gate pass
    # on a cohort unlike the traffic that the change will actually see.
    return selected[:sample_count]


def cap_body_text(
    text: str,
    *,
    head_chars: int = BODY_CAP_HEAD,
    tail_chars: int = BODY_CAP_TAIL,
    joiner: str = BODY_CAP_JOINER,
) -> str:
    """Return ``text`` capped to head + tail with an ellipsis joiner."""

    head = max(0, int(head_chars))
    tail = max(0, int(tail_chars))
    if len(text) <= head + tail:
        return text
    if tail == 0:
        return text[:head] + joiner
    return text[:head] + joiner + text[-tail:]


def body_cap_affected_count(rows: Sequence[Mapping[str, Any]]) -> int:
    """Count candidates whose model-visible body changes under production caps."""

    return sum(
        cap_body_text(str(row.get("body_text") or "")) != str(row.get("body_text") or "")
        for row in rows
    )


@contextmanager
def replay_call_attribution(
    *,
    pair_kind: str,
    repeat: int,
    logical_run: str,
    arm: str,
) -> Iterator[None]:
    """Attribute every nested LLM/embedding call to one logical replay run."""

    token = _REPLAY_ATTRIBUTION.set(
        {
            "pair_kind": pair_kind,
            "repeat": int(repeat),
            "logical_run": logical_run,
            "arm": arm,
        }
    )
    try:
        yield
    finally:
        _REPLAY_ATTRIBUTION.reset(token)


@contextmanager
def configured_topic_lifecycle_serialization(config: object) -> Iterator[bool]:
    """Mirror the API/CLI archived-topic serialization switch for replay."""

    from openbiliclaw.soul.profile_views import (
        set_topic_lifecycle_serialization,
        topic_lifecycle_serialization_enabled,
    )

    previous = topic_lifecycle_serialization_enabled()
    configured = (
        str(
            getattr(
                getattr(config, "soul", None),
                "topic_lifecycle_serialization",
                "off",
            )
        )
        .strip()
        .lower()
        == "on"
    )
    set_topic_lifecycle_serialization(configured)
    try:
        yield configured
    finally:
        set_topic_lifecycle_serialization(previous)


def validate_replay_prefilter_compatibility(config: object) -> str:
    """Return production prefilter mode or reject behavior-changing enforce runs."""

    mode = (
        str(
            getattr(getattr(config, "discovery", None), "eval_prefilter_mode", "shadow") or "shadow"
        )
        .strip()
        .lower()
    )
    if mode not in {"off", "shadow", "enforce"}:
        mode = "shadow"
    if mode == "enforce":
        raise RuntimeError(
            "Replay isolates model-visible diet changes with eval_prefilter_mode=off, but "
            "production config is enforce. Switch production back to shadow/off before "
            "collecting landing evidence; otherwise the replay cohort is not equivalent."
        )
    return mode


@contextmanager
def legacy_body_text_prompt_caps() -> Iterator[None]:
    """Disable evaluation body caps at prompt construction for body-cap arm A.

    Candidates remain byte-for-byte unchanged. This preserves the production
    description/body dedup relation and changes only the model-visible cap.
    """

    from openbiliclaw.discovery import engine as engine_module

    original_head = engine_module._EVALUATION_BODY_TEXT_HEAD_CAP
    original_tail = engine_module._EVALUATION_BODY_TEXT_TAIL_CAP
    engine_module._EVALUATION_BODY_TEXT_HEAD_CAP = sys.maxsize
    engine_module._EVALUATION_BODY_TEXT_TAIL_CAP = 0
    try:
        yield
    finally:
        engine_module._EVALUATION_BODY_TEXT_HEAD_CAP = original_head
        engine_module._EVALUATION_BODY_TEXT_TAIL_CAP = original_tail


def _embedding_namespace(service: object) -> str:
    for attribute in (
        "cache_model_namespace",
        "embedding_fingerprint",
        "embedding_model",
    ):
        value = str(getattr(service, attribute, "") or "").strip()
        if value:
            return value
    service_type = type(service)
    return _digest(f"{service_type.__module__}.{service_type.__qualname__}")[:32]


def _validated_embedding_vector(value: object) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ReplayEmbeddingValidationError("embedding returned an empty or non-list vector")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ReplayEmbeddingValidationError("embedding vector contained a non-numeric value")
        number = float(item)
        if not math.isfinite(number):
            raise ReplayEmbeddingValidationError("embedding vector contained NaN or infinity")
        vector.append(number)
    return vector


def _embedding_error_reason(exc: BaseException) -> str:
    if isinstance(exc, ReplayEmbeddingValidationError):
        return str(exc)
    return f"embedding request raised {type(exc).__name__}"


# ``--arm-b reason-diet``: arm A restores the pre-2689d412 reason instruction
# (unconditional one-sentence reasons) by surgically swapping the exact new
# snippets back to the legacy text inside the current prompt constants; arm B
# runs the production reason contract (skip <0.5, ≤30字). Each (current, legacy)
# pair must match the live constant verbatim — the guard below fails loudly if
# a later prompt edit breaks the swap, instead of silently comparing A to A.
_REASON_DIET_SWAPS: tuple[tuple[str, str, str], ...] = (
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "3. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
        'score 严格低于 0.5 的条目,reason 必须写成空串 ""'
        "(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);"
        "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
        "不超过 30 个 Unicode 字符,说明内容与画像匹配或不匹配的依据。\n",
        "3. reason 只写一句中文,解释为什么这个人会喜欢或不喜欢这个内容。\n",
    ),
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '  "reason": "主题契合画像中的长期兴趣,内容角度有增量",\n',
        '  "reason": "这个视频的选题角度新颖,节奏轻快,契合你对该领域的好奇心。",\n',
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "、reason、topic_group(2-4词粗分类)、style_key(13选1)、",
        "、reason(一句中文)、topic_group(2-4词粗分类)、style_key(13选1)、",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "3a. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
        'score 严格低于 0.5 的条目,reason 必须写成空串 ""'
        "(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);"
        "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
        "不超过 30 个 Unicode 字符,说明内容与画像匹配的依据。\n",
        "",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '"score": 0.45, "reason": ""',
        '"score": 0.45, "reason": "..."',
    ),
)


@contextmanager
def legacy_reason_prompts() -> Iterator[None]:
    """Temporarily restore the pre-reason-diet evaluation prompts (arm A)."""

    from openbiliclaw.llm import prompts as prompts_module

    originals: dict[str, str] = {}
    patched: dict[str, str] = {}
    for constant_name, current_snippet, legacy_snippet in _REASON_DIET_SWAPS:
        base = patched.get(constant_name, getattr(prompts_module, constant_name))
        if constant_name not in originals:
            originals[constant_name] = getattr(prompts_module, constant_name)
        if current_snippet not in base:
            raise RuntimeError(
                "reason-diet arm is stale: expected snippet not found in "
                f"{constant_name}; update _REASON_DIET_SWAPS to match the live prompt."
            )
        patched[constant_name] = base.replace(current_snippet, legacy_snippet, 1)
    for constant_name, text in patched.items():
        setattr(prompts_module, constant_name, text)
    try:
        yield
    finally:
        for constant_name, text in originals.items():
            setattr(prompts_module, constant_name, text)


def parse_model_override(raw_arm: str) -> ModelOverride | None:
    """Parse a model arm value, returning None for non-model arms."""

    if not raw_arm.startswith("model="):
        return None
    value = raw_arm.removeprefix("model=").strip()
    provider, sep, model = value.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or (sep and not model):
        raise ValueError(
            "--arm-b model override must be model=<instance-id> for v2 routing "
            "or model=<provider:model> for legacy routing"
        )
    return ModelOverride(provider=provider, model=model)


def _absolute_deltas(scores_a: Sequence[float], scores_b: Sequence[float]) -> list[float]:
    _validate_aligned_scores(scores_a, scores_b)
    return [abs(float(left) - float(right)) for left, right in zip(scores_a, scores_b, strict=True)]


def _validate_aligned_scores(scores_a: Sequence[float], scores_b: Sequence[float]) -> None:
    if len(scores_a) != len(scores_b):
        raise ValueError("score lists must have the same length")


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted((float(value), index) for index, value in enumerate(values))
    ranks = [0.0] * len(indexed)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][0] == indexed[start][0]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for _value, original_index in indexed[start:end]:
            ranks[original_index] = average_rank
        start = end
    return ranks


def _candidate_score_threshold(candidate: object) -> float:
    if isinstance(candidate, Mapping):
        raw_threshold = candidate.get("score_threshold")
    else:
        raw_threshold = getattr(candidate, "score_threshold", 0.0)
    try:
        threshold = float(raw_threshold or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return threshold if threshold > 0 else 0.0


def _candidate_admission_threshold(
    candidate: object,
    *,
    admission_min_score: float,
) -> float:
    from openbiliclaw.discovery.admission import effective_admission_threshold

    requested = _candidate_score_threshold(candidate)
    return effective_admission_threshold(
        _candidate_strategy(candidate),
        admission_min_score,
        requested if requested > 0 else None,
    )


def _candidate_strategy(candidate: object) -> str:
    if isinstance(candidate, Mapping):
        raw_strategy = candidate.get("source_strategy")
    else:
        raw_strategy = getattr(candidate, "source_strategy", "")
    strategy = str(raw_strategy or "default").strip().lower()
    return strategy or "default"


def _normalize_platform(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "bili": "bilibili",
        "b站": "bilibili",
        "xhs": "xiaohongshu",
        "dy": "douyin",
        "yt": "youtube",
        "x": "twitter",
    }
    return aliases.get(raw, raw)


def _candidate_row_sort_key(row: Mapping[str, Any]) -> tuple[str, int]:
    timestamp = str(
        row.get("evaluated_at")
        or row.get("cached_at")
        or row.get("last_seen_at")
        or row.get("created_at")
        or ""
    )
    return timestamp, _to_int(row.get("id"))


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _row_to_replay_candidate(row: Mapping[str, Any]) -> ReplayCandidate:
    content_id = str(row.get("content_id") or row.get("bvid") or "").strip()
    return ReplayCandidate(
        candidate_id=_to_int(row.get("id")),
        title=str(row.get("title") or ""),
        source_strategy=str(row.get("source_strategy") or ""),
        source_platform=_normalize_platform(row.get("source_platform")),
        content_id=content_id,
        content_url=str(row.get("content_url") or ""),
        body_text=str(row.get("body_text") or ""),
        score_threshold=float(row.get("score_threshold") or 0.0),
    )


def _read_only_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _load_read_only_database(db_path: Path) -> Any:
    from openbiliclaw.storage.database import Database

    database = Database(db_path)
    database._conn = _read_only_sqlite_connection(db_path)  # noqa: SLF001
    return database


def _database_path(config: object) -> Path:
    data_path = Path(getattr(config, "data_path", PROJECT_ROOT / "data"))
    return data_path / "openbiliclaw.db"


def _fetch_replay_rows(database: Any, *, sample: int, platform: str | None) -> list[dict[str, Any]]:
    limit = max(sample * 4, sample, 100)
    platform_filter = _normalize_platform(platform) if platform else ""
    params: list[object] = ["evaluated", "cached", "rejected_low_score"]
    platform_clause = ""
    if platform_filter:
        platform_clause = "AND lower(source_platform) = ?"
        params.append(platform_filter)
    params.append(limit)
    cursor = database.conn.execute(
        f"""
        SELECT *
        FROM discovery_candidates
        WHERE status IN (?, ?, ?)
          {platform_clause}
        ORDER BY COALESCE(evaluated_at, cached_at, last_seen_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    selected = select_replay_rows(rows, sample=sample, platform=platform)
    if len(selected) != sample:
        raise RuntimeError(
            f"Replay requires exactly {sample} candidates, but only {len(selected)} eligible "
            "evaluated/cached/rejected_low_score rows were available."
        )
    return selected


def _profile_digest_payload(profile: object) -> dict[str, object]:
    serialized = profile.to_dict() if callable(getattr(profile, "to_dict", None)) else str(profile)
    speculations = getattr(profile, "_active_speculations", None)
    speculation_payload = [
        item.to_dict() if callable(getattr(item, "to_dict", None)) else str(item)
        for item in (speculations if isinstance(speculations, list) else [])
    ]
    return {"profile": serialized, "active_speculations": speculation_payload}


def _load_profile_snapshot(data_root: Path) -> ReplayProfileSnapshot:
    """Load the exact effective profile shape exposed by SoulEngine.get_profile."""

    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.overrides import apply_overrides
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.speculator import load_speculative_state

    memory = MemoryManager(data_root)
    soul_layer = memory.get_layer("soul")
    soul_layer.load()
    if not soul_layer.data:
        raise RuntimeError(
            f"No current soul profile found in {data_root / 'memory' / 'soul.json'}."
        )
    raw_profile = OnionProfile.from_dict(dict(soul_layer.data))
    overrides = memory.load_profile_overrides()
    effective_profile = apply_overrides(raw_profile, overrides)
    active_speculations = [
        item for item in load_speculative_state(data_root).active if item.status == "active"
    ]
    if active_speculations:
        effective_profile._active_speculations = active_speculations  # type: ignore[attr-defined]
    return ReplayProfileSnapshot(
        raw_profile=raw_profile,
        effective_profile=effective_profile,
        raw_digest=_digest(_profile_digest_payload(raw_profile)),
        effective_digest=_digest(_profile_digest_payload(effective_profile)),
        overrides_present=not overrides.is_empty(),
        active_speculation_count=len(active_speculations),
    )


def _load_current_profile(data_root: Path) -> object:
    """Backward-compatible helper returning the effective replay profile."""

    return _load_profile_snapshot(data_root).effective_profile


def _load_memory_for_llm(data_root: Path) -> object:
    from openbiliclaw.memory.manager import MemoryManager

    memory = MemoryManager(data_root)
    for layer_name in ("soul", "preference"):
        try:
            memory.get_layer(layer_name).load()
        except Exception:
            logger.debug(
                "Failed to load memory layer %s for LLM service", layer_name, exc_info=True
            )
    return memory


def _recent_negative_exemplars(database: Any) -> list[dict[str, object]] | None:
    from openbiliclaw.soul.negative_exemplars import recent_negative_exemplars

    exemplars = recent_negative_exemplars(database)
    if not exemplars:
        return None
    return [dict(item) for item in exemplars]


def _build_llm_service(
    config: object,
    data_root: Path,
    *,
    model_override: ModelOverride | None = None,
) -> object:
    from openbiliclaw.config import llm_concurrency_from_config
    from openbiliclaw.llm.registry import build_llm_registry
    from openbiliclaw.llm.service import LLMService, ModuleOverride, module_overrides_from_config

    registry = build_llm_registry(config)
    module_overrides = dict(module_overrides_from_config(config))
    if model_override is not None:
        if not registry.is_chat_capable(model_override.provider):
            raise RuntimeError(
                f"Provider {model_override.provider!r} is not registered/chat-capable."
            )
        if bool(getattr(getattr(config, "llm", None), "instance_routing", False)):
            if model_override.model:
                raise RuntimeError(
                    "v2 instance routing binds the model in [llm.instances]; "
                    "use --arm-b model=<instance-id> without :model"
                )
            module_overrides["evaluation"] = ModuleOverride(
                chain=(model_override.provider,),
                custom_chain=True,
            )
        else:
            if not model_override.model:
                raise RuntimeError("legacy routing requires --arm-b model=<provider:model>")
            module_overrides["evaluation"] = ModuleOverride(
                provider=model_override.provider,
                model=model_override.model,
            )
    return LLMService(
        registry=registry,
        memory=_load_memory_for_llm(data_root),
        module_overrides=module_overrides,
        concurrency=llm_concurrency_from_config(config),
    )


def _build_embedding_service(config: object) -> object | None:
    from openbiliclaw.llm.registry import build_embedding_service, build_llm_registry

    registry = build_llm_registry(config)
    return build_embedding_service(config, registry)


def _configured_embedding_provider(config: object) -> str:
    llm_config = getattr(config, "llm", None)
    embedding_config = getattr(llm_config, "embedding", None)
    primary = str(getattr(embedding_config, "provider", "") or "").strip().lower()
    fallback = str(getattr(embedding_config, "fallback_provider", "") or "").strip().lower()
    return primary or fallback


@contextmanager
def run_scoped_embedding_audit(
    config: object,
    *,
    allow_no_embedding: bool,
) -> Iterator[ReplayEmbeddingAudit | None]:
    """Build a replay-only embedding cache that remains alive for the full run."""

    original_data_dir = getattr(config, "data_dir", None)
    configured_provider = _configured_embedding_provider(config)
    try:
        with TemporaryDirectory(prefix="openbiliclaw-replay-embedding-") as cache_dir:
            config.data_dir = cache_dir  # type: ignore[attr-defined]
            service = _build_embedding_service(config)
            if service is None:
                if configured_provider:
                    raise RuntimeError(
                        "Configured embedding provider could not be constructed; "
                        "replay cannot treat this as a zero-recall observation."
                    )
                if not allow_no_embedding:
                    raise RuntimeError(
                        "Embedding is disabled in production config. Replay requires a usable "
                        "embedding service by default; pass --allow-no-embedding only to emit "
                        "a degraded, non-landing artifact."
                    )
                yield None
                return

            audit = ReplayEmbeddingAudit(service)
            try:
                yield audit
            finally:
                l2_cache = getattr(service, "_l2_cache", None)
                close_cache = getattr(l2_cache, "close", None)
                if callable(close_cache):
                    close_cache()
    finally:
        config.data_dir = original_data_dir  # type: ignore[attr-defined]


class _DeterministicLLMService:
    """Force temperature=0 so the replay measures prompt changes, not sampling noise.

    The production evaluator samples at the provider default temperature and
    repeated A/A runs show material gateway/model noise. Pinning temperature
    does not eliminate that noise, so the gate still measures an empirical
    repeated A/A envelope around both treatment arms.
    """

    def __init__(self, inner: object, *, service: str = "") -> None:
        self._inner = inner
        self._service = service
        self.calls: list[dict[str, object]] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def complete_structured_task(self, **kwargs: Any) -> object:
        return await self._complete("complete_structured_task", kwargs)

    async def complete_multimodal_structured_task(self, **kwargs: Any) -> object:
        return await self._complete("complete_multimodal_structured_task", kwargs)

    async def _complete(self, method_name: str, kwargs: dict[str, Any]) -> object:
        kwargs["temperature"] = 0.0
        # Keep the replay production-equivalent. If a gateway burns the 4096
        # budget on hidden reasoning and emits no structured response, the gate
        # must fail rather than masking a production failure with extra headroom.
        kwargs["max_tokens"] = 4096
        method = getattr(self._inner, method_name)
        attribution = dict(_REPLAY_ATTRIBUTION.get())
        try:
            response = await method(**kwargs)
        except Exception as exc:
            retryable_rate_limit = _is_retryable_replay_rate_limit(exc)
            self.calls.append(
                {
                    "service": self._service,
                    **attribution,
                    "method": method_name,
                    "caller": str(kwargs.get("caller") or ""),
                    "provider": "",
                    "instance_id": "",
                    "model": "",
                    "temperature": 0.0,
                    "max_tokens": 4096,
                    "usage": None,
                    "status": "error",
                    "error_kind": (
                        "transient_rate_limit" if retryable_rate_limit else type(exc).__name__
                    ),
                }
            )
            raise
        usage = getattr(response, "usage", None)
        self.calls.append(
            {
                "service": self._service,
                **attribution,
                "method": method_name,
                "caller": str(kwargs.get("caller") or ""),
                "provider": str(getattr(response, "provider", "") or ""),
                "instance_id": str(getattr(response, "instance_id", "") or ""),
                "model": str(getattr(response, "model", "") or ""),
                "temperature": 0.0,
                "max_tokens": 4096,
                "usage": dict(usage) if isinstance(usage, Mapping) else None,
                "status": "ok",
            }
        )
        return response


def _expected_evaluation_instance(service: object) -> str:
    inner = getattr(service, "_inner", service)
    resolve_chain = getattr(inner, "_resolve_module_chain", None)
    if callable(resolve_chain):
        chain = resolve_chain("discovery.evaluate_batch")
        if isinstance(chain, Sequence) and not isinstance(chain, str | bytes) and chain:
            return str(chain[0]).strip().lower()
    resolve_override = getattr(inner, "_resolve_module_override", None)
    if callable(resolve_override):
        override = resolve_override("discovery.evaluate_batch")
        if isinstance(override, tuple) and override:
            return str(override[0]).strip().lower()
    registry = getattr(inner, "registry", None)
    return str(getattr(registry, "default_provider", "") or "").strip().lower()


def validate_replay_routes(
    calls: Sequence[Mapping[str, object]],
    *,
    repeats: int,
    model_override: ModelOverride | None,
    expected_control_instance: str = "",
    expected_treatment_instance: str = "",
) -> dict[str, object]:
    """Require one actual route per logical run and arm-equivalent routing."""

    expected: dict[tuple[str, int, str], str] = {}
    for repeat in range(1, repeats + 1):
        expected[("control", repeat, "A1")] = "A"
        expected[("control", repeat, "A2")] = "A"
        expected[("treatment", repeat, "A")] = "A"
        expected[("treatment", repeat, "B")] = "B"

    grouped: dict[tuple[str, int, str], list[Mapping[str, object]]] = {}
    blocking_reasons: list[str] = []
    for call in calls:
        key = (
            str(call.get("pair_kind") or ""),
            _to_int(call.get("repeat")),
            str(call.get("logical_run") or ""),
        )
        if key not in expected:
            blocking_reasons.append(f"LLM call has missing/invalid replay attribution: {key!r}")
            continue
        if str(call.get("arm") or "") != expected[key]:
            blocking_reasons.append(f"LLM call arm attribution mismatch for {key!r}")
        grouped.setdefault(key, []).append(call)

    run_payloads: list[dict[str, object]] = []
    route_by_run: dict[tuple[str, int, str], tuple[str, str, str]] = {}
    for key, expected_arm in expected.items():
        run_calls = grouped.get(key, [])
        if not run_calls:
            blocking_reasons.append(f"logical run {key!r} emitted no LLM call")
            continue
        successful_calls = [call for call in run_calls if call.get("status") == "ok"]
        failed_calls = [call for call in run_calls if call.get("status") != "ok"]
        recovered_rate_limit_calls = [
            call for call in failed_calls if call.get("error_kind") == "transient_rate_limit"
        ]
        fatal_failed_calls = [
            call for call in failed_calls if call.get("error_kind") != "transient_rate_limit"
        ]
        routes = {
            (
                str(call.get("provider") or "").strip(),
                str(call.get("instance_id") or "").strip(),
                str(call.get("model") or "").strip(),
            )
            for call in successful_calls
        }
        if fatal_failed_calls:
            blocking_reasons.append(f"logical run {key!r} contains a fatal failed LLM call")
        if failed_calls and len(recovered_rate_limit_calls) != len(failed_calls):
            blocking_reasons.append(f"logical run {key!r} contains an unaudited failed LLM call")
        if not successful_calls:
            blocking_reasons.append(f"logical run {key!r} emitted no successful LLM call")
            continue
        if any(not all(route) for route in routes):
            blocking_reasons.append(f"logical run {key!r} contains an empty actual route")
        if len(routes) != 1:
            blocking_reasons.append(f"logical run {key!r} mixed {len(routes)} actual routes")
        route = next(iter(routes))
        route_by_run[key] = route
        run_payloads.append(
            {
                "pair_kind": key[0],
                "repeat": key[1],
                "logical_run": key[2],
                "arm": expected_arm,
                "call_count": len(run_calls),
                "successful_call_count": len(successful_calls),
                "recovered_rate_limit_call_count": len(recovered_rate_limit_calls),
                "route": {
                    "provider": route[0],
                    "instance_id": route[1],
                    "model": route[2],
                },
            }
        )

    baseline_routes = {
        route
        for key, route in route_by_run.items()
        if key[0] == "control" or (key[0] == "treatment" and key[2] == "A")
    }
    if len(baseline_routes) != 1:
        blocking_reasons.append(
            "control A/A and treatment A did not use one identical actual route"
        )
    elif expected_control_instance:
        baseline_route = next(iter(baseline_routes))
        if baseline_route[1] != expected_control_instance:
            blocking_reasons.append(
                "control route unexpectedly failed over from configured instance "
                f"{expected_control_instance!r} to {baseline_route[1]!r}"
            )

    treatment_b_routes = {
        route for key, route in route_by_run.items() if key[0] == "treatment" and key[2] == "B"
    }
    if len(treatment_b_routes) != 1:
        blocking_reasons.append("treatment B did not use one stable actual route")
    else:
        treatment_b_route = next(iter(treatment_b_routes))
        if expected_treatment_instance and treatment_b_route[1] != expected_treatment_instance:
            blocking_reasons.append(
                "treatment route unexpectedly failed over from configured instance "
                f"{expected_treatment_instance!r} to {treatment_b_route[1]!r}"
            )
    if len(treatment_b_routes) == 1:
        treatment_b_route = next(iter(treatment_b_routes))
        if model_override is None:
            if baseline_routes != treatment_b_routes:
                blocking_reasons.append("non-model experiment drifted route between arms A and B")
        else:
            if model_override.model:
                if (
                    treatment_b_route[0] != model_override.provider
                    or treatment_b_route[2] != model_override.model
                ):
                    blocking_reasons.append(
                        "legacy model treatment did not use the requested provider/model"
                    )
            elif treatment_b_route[1] != model_override.provider:
                blocking_reasons.append(
                    "instance-routed model treatment did not use the requested instance"
                )

    unique_reasons = list(dict.fromkeys(blocking_reasons))
    return {
        "passed": not unique_reasons,
        "blocking_reasons": unique_reasons,
        "recovered_rate_limit_call_count": sum(
            int(run.get("recovered_rate_limit_call_count") or 0) for run in run_payloads
        ),
        "logical_runs": run_payloads,
    }


def _build_engine(
    llm_service: object,
    config: object,
    *,
    compact_profile: bool,
    negative_examples: list[dict[str, object]] | None,
    legacy_profile: bool,
    embedding_service: object | None,
    recall_audit: ReplayRecallAudit | None = None,
) -> object:
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
        _BatchRelatedInterestRecall,
        _RelatedInterestRecall,
    )
    from openbiliclaw.discovery.strategies._utils import build_profile_summary

    class ReplayDiscoveryEngine(ContentDiscoveryEngine):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._replay_negative_examples = negative_examples
            self._replay_compact_profile = compact_profile
            self._replay_legacy_profile = legacy_profile
            self._replay_recall_audit = recall_audit
            super().__init__(*args, **kwargs)

        def _get_eval_cache_entry(self, cache_key: str) -> None:
            return None

        def _set_eval_cache_entry(self, cache_key: str, entry: object) -> None:
            return None

        def _get_negative_exemplars(self) -> list[dict[str, object]] | None:
            examples = getattr(self, "_replay_negative_examples", None)
            if not examples:
                return None
            return [dict(item) for item in examples]

        def _recent_viewed_content_keys(self) -> set[str]:
            return set()

        def _evaluation_profile_summary(self, profile: object) -> dict[str, object]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return build_profile_summary(profile)
            # Production owns the compact view. Applying the transform again
            # would let replay drift if it ever stops being idempotent.
            return ContentDiscoveryEngine._evaluation_profile_summary(profile)

        async def _related_interests_for_content(
            self,
            content: object,
            profile: object,
            *,
            top_k: int = 3,
        ) -> list[str]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return []
            return await super()._related_interests_for_content(content, profile, top_k=top_k)

        async def _related_interests_for_content_result(
            self,
            content: object,
            profile: object,
            *,
            top_k: int = 3,
        ) -> _RelatedInterestRecall:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return _RelatedInterestRecall([], True)
            result = await super()._related_interests_for_content_result(
                content,
                profile,
                top_k=top_k,
            )
            audit = getattr(self, "_replay_recall_audit", None)
            if isinstance(audit, ReplayRecallAudit):
                audit.record_single(result.related, complete=result.complete)
            return result

        async def _related_interests_for_batch(
            self,
            contents: Sequence[object],
            profile: object,
            *,
            top_k: int = 3,
        ) -> dict[int, list[str]]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return {}
            return await super()._related_interests_for_batch(contents, profile, top_k=top_k)

        async def _related_interests_for_batch_result(
            self,
            contents: Sequence[object],
            profile: object,
            *,
            top_k: int = 3,
        ) -> _BatchRelatedInterestRecall:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return _BatchRelatedInterestRecall({}, frozenset(range(len(contents))))
            result = await super()._related_interests_for_batch_result(
                contents,
                profile,
                top_k=top_k,
            )
            audit = getattr(self, "_replay_recall_audit", None)
            if isinstance(audit, ReplayRecallAudit):
                audit.record_batch(
                    result.related_by_index,
                    candidate_count=len(contents),
                    complete_candidate_count=len(result.complete_indices),
                )
            return result

    discovery_cfg = getattr(config, "discovery", None)
    return ReplayDiscoveryEngine(
        llm_service=llm_service,
        database=None,
        concurrency=DiscoveryConcurrencyController(llm_evaluation_concurrency=2),
        embedding_service=embedding_service,
        multimodal_evaluation_enabled=bool(
            getattr(discovery_cfg, "multimodal_evaluation_enabled", False)
        ),
        multimodal_batch_size=int(getattr(discovery_cfg, "multimodal_batch_size", 8)),
        multimodal_image_max_px=int(getattr(discovery_cfg, "multimodal_image_max_px", 384)),
        multimodal_image_quality=int(getattr(discovery_cfg, "multimodal_image_quality", 72)),
        multimodal_image_timeout_seconds=int(
            getattr(discovery_cfg, "multimodal_image_timeout_seconds", 6)
        ),
        eval_prefilter_mode="off",
    )


def _rows_to_contents(rows: Sequence[Mapping[str, Any]]) -> list[Any]:
    from openbiliclaw.discovery.candidate_pool import row_to_discovered_content

    return [row_to_discovered_content(dict(row)) for row in rows]


async def _score_contents(
    engine: object,
    contents: Sequence[Any],
    profile: object,
    *,
    source_context: str,
) -> list[float]:
    if not contents:
        return []
    # Match the current API coordinator's production claim size (30), not the
    # engine's 90-item hard cap: each chunk carries
    # its own recall-embedding warm-up, so smaller chunks keep the per-chunk
    # timeout budget meaningful.
    hard_cap = max(1, int(getattr(engine, "_EVALUATE_BATCH_HARD_CAP", 90) or 90))
    hard_cap = min(hard_cap, _DEFAULT_BATCH_SIZE)
    scores: list[float] = []
    evaluate = getattr(engine, "evaluate_content_batch", None)
    if not callable(evaluate):
        raise RuntimeError("Replay engine does not expose evaluate_content_batch")
    for start in range(0, len(contents), hard_cap):
        chunk = list(contents[start : start + hard_cap])
        initial_evaluation_state = [
            {
                field: getattr(content, field)
                for field in _REPLAY_EVALUATION_OUTPUT_FIELDS
                if hasattr(content, field)
            }
            for content in chunk
        ]
        # Hard deadline per chunk: a stalled provider/gateway must fail the
        # gate run loudly instead of hanging it forever (observed in prod:
        # one stuck upstream session blocked an un-timeboxed call for 2h+).
        for attempt in range(len(RATE_LIMIT_RETRY_DELAYS_SECONDS) + 1):
            try:
                chunk_scores = await asyncio.wait_for(
                    evaluate(
                        chunk,
                        profile,
                        source_context=source_context,
                        batch_size=_DEFAULT_BATCH_SIZE,
                    ),
                    timeout=CHUNK_TIMEOUT_SECONDS,
                )
                break
            except TimeoutError as exc:
                raise RuntimeError(
                    f"Evaluation chunk timed out after {CHUNK_TIMEOUT_SECONDS}s "
                    f"({source_context}, items {start}..{start + len(chunk) - 1}); "
                    "check the LLM provider/gateway and rerun."
                ) from exc
            except Exception as exc:
                if not _is_retryable_replay_rate_limit(exc) or attempt >= len(
                    RATE_LIMIT_RETRY_DELAYS_SECONDS
                ):
                    raise
                for content, state in zip(chunk, initial_evaluation_state, strict=True):
                    for field, value in state.items():
                        setattr(content, field, value)
                delay = RATE_LIMIT_RETRY_DELAYS_SECONDS[attempt]
                logger.warning(
                    "Replay chunk hit a transient provider rate limit; retrying items "
                    "%d..%d after %.0fs (%d/%d)",
                    start,
                    start + len(chunk) - 1,
                    delay,
                    attempt + 1,
                    len(RATE_LIMIT_RETRY_DELAYS_SECONDS),
                )
                await asyncio.sleep(delay)
        if len(chunk_scores) != len(chunk):
            raise RuntimeError(
                "Evaluation returned an incomplete score vector "
                f"({source_context}, expected {len(chunk)}, got {len(chunk_scores)}); "
                "the replay gate is invalid."
            )
        missing_ids = [
            str(getattr(content, "content_id", "") or getattr(content, "title", "") or index)
            for index, content in enumerate(chunk, start=start)
            if str(getattr(content, "relevance_reason", "") or "") == "evaluation_response_missing"
        ]
        if missing_ids:
            preview = ", ".join(missing_ids[:5])
            suffix = "..." if len(missing_ids) > 5 else ""
            raise RuntimeError(
                "Evaluation response was missing after retries for "
                f"{len(missing_ids)} item(s) ({preview}{suffix}); "
                "gateway/parse failures cannot be counted as zero-score observations."
            )
        scores.extend(float(score) for score in chunk_scores[: len(chunk)])
    return scores


def _top_delta_items(
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    *,
    limit: int = 10,
) -> list[tuple[float, float, ReplayCandidate]]:
    rows = [
        (abs(float(score_b) - float(score_a)), float(score_b) - float(score_a), candidate)
        for candidate, score_a, score_b in zip(candidates, scores_a, scores_b, strict=True)
    ]
    rows.sort(key=lambda item: (item[0], abs(item[1]), item[2].candidate_id), reverse=True)
    return rows[:limit]


def _admit_count(
    candidates: Sequence[ReplayCandidate],
    scores: Sequence[float],
    *,
    admission_min_score: float,
) -> int:
    return sum(
        score
        >= _candidate_admission_threshold(
            candidate,
            admission_min_score=admission_min_score,
        )
        for candidate, score in zip(candidates, scores, strict=True)
    )


def _pair_metrics(
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    *,
    admission_min_score: float,
) -> ReplayMetrics:
    delta = score_delta_summary(scores_a, scores_b)
    flips = admission_flip_summary(
        candidates,
        scores_a,
        scores_b,
        admission_min_score=admission_min_score,
    )
    admitted_a = _admit_count(
        candidates,
        scores_a,
        admission_min_score=admission_min_score,
    )
    admitted_b = _admit_count(
        candidates,
        scores_b,
        admission_min_score=admission_min_score,
    )
    item_count = len(candidates)
    return ReplayMetrics(
        mean_abs_delta=delta.mean_abs_delta,
        p95_abs_delta=delta.p95_abs_delta,
        spearman=spearman_rank_correlation(scores_a, scores_b),
        flip_rate=flips.flip_rate,
        flip_count=flips.flip_count,
        admitted_a=admitted_a,
        admitted_b=admitted_b,
        admission_rate_delta=((admitted_b - admitted_a) / item_count if item_count else 0.0),
    )


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(len(ordered) * max(0.0, min(1.0, percentile))))
    return ordered[min(len(ordered) - 1, rank - 1)]


def relative_gate(
    control_pairs: Sequence[ReplayPair],
    treatment_pairs: Sequence[ReplayPair],
) -> tuple[bool, dict[str, float]]:
    """Compare treatment medians with an empirical repeated A/A envelope."""

    if len(control_pairs) < 3 or len(treatment_pairs) < 3:
        raise ValueError("relative gate requires at least three control and treatment pairs")
    control_flip_ceiling = max(
        FLIP_RATE_MAX,
        _nearest_rank_percentile(
            [pair.metrics.flip_rate for pair in control_pairs],
            0.95,
        ),
    )
    control_spearman_floor = min(
        SPEARMAN_MIN,
        _nearest_rank_percentile(
            [pair.metrics.spearman for pair in control_pairs],
            0.05,
        ),
    )
    control_admission_delta = median(pair.metrics.admission_rate_delta for pair in control_pairs)
    treatment_flip = median(pair.metrics.flip_rate for pair in treatment_pairs)
    treatment_spearman = median(pair.metrics.spearman for pair in treatment_pairs)
    treatment_admission_delta = median(
        pair.metrics.admission_rate_delta for pair in treatment_pairs
    )
    admission_floor = control_admission_delta - _RELATIVE_ADMISSION_SHRINK_MAX
    gate_passed = (
        treatment_flip <= control_flip_ceiling
        and treatment_spearman >= control_spearman_floor
        and treatment_admission_delta >= admission_floor
    )
    return gate_passed, {
        "control_flip_ceiling": control_flip_ceiling,
        "control_spearman_floor": control_spearman_floor,
        "control_admission_delta": control_admission_delta,
        "treatment_flip_median": treatment_flip,
        "treatment_spearman_median": treatment_spearman,
        "treatment_admission_delta_median": treatment_admission_delta,
        "admission_delta_floor": admission_floor,
    }


def replay_blocking_reasons(
    *,
    quality_passed: bool,
    route_audit: Mapping[str, object],
    embedding_audit: Mapping[str, object],
    recall_audit: Mapping[str, object],
    body_cap: bool,
    body_cap_affected: int,
    body_cap_contract_matches: bool,
    profile_snapshot_stable: bool,
    candidate_snapshot_stable: bool,
) -> list[str]:
    """Return every independent reason that invalidates landing evidence."""

    blocking_reasons: list[str] = []
    if not quality_passed:
        blocking_reasons.append("relative quality gate failed")

    for label, audit in (
        ("route", route_audit),
        ("embedding", embedding_audit),
        ("recall", recall_audit),
    ):
        if not bool(audit.get("passed")):
            blocking_reasons.append(f"{label} audit failed")
        blocking_reasons.extend(str(reason) for reason in audit.get("blocking_reasons", []))

    if body_cap and body_cap_affected <= 0:
        blocking_reasons.append(
            "body-cap experiment had zero candidates affected by the production cap"
        )
    if body_cap and not body_cap_contract_matches:
        blocking_reasons.append(
            "live production body caps no longer match replay contract "
            f"({BODY_CAP_HEAD}+{BODY_CAP_TAIL})"
        )
    if not profile_snapshot_stable:
        blocking_reasons.append("effective profile snapshot drifted during replay")
    if not candidate_snapshot_stable:
        blocking_reasons.append("candidate snapshot drifted during replay")
    return list(dict.fromkeys(blocking_reasons))


def _print_report(
    *,
    arm_b: str,
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    platform: str | None,
    recall_note: str = "",
    admission_min_score: float = 0.60,
) -> bool:
    delta = score_delta_summary(scores_a, scores_b)
    spearman = spearman_rank_correlation(scores_a, scores_b)
    flips = admission_flip_summary(
        candidates,
        scores_a,
        scores_b,
        admission_min_score=admission_min_score,
    )
    gate_passed = flips.flip_rate <= FLIP_RATE_MAX and spearman >= SPEARMAN_MIN

    print("\nProfile Diet A/B Replay")
    print(f"  sample: {len(candidates)}")
    print(f"  platform: {platform or 'all'}")
    arm_a_label = "legacy full-profile/no-recall" if arm_b == "compact" else "production"
    print(f"  arm A: {arm_a_label}")
    print(f"  arm B: {arm_b}")
    if recall_note:
        print(f"  note: {recall_note}")
    print()
    print("Metrics")
    print(f"  mean |delta|: {delta.mean_abs_delta:.4f}")
    print(f"  p95  |delta|: {delta.p95_abs_delta:.4f}")
    print(f"  Spearman:     {spearman:.4f}  (gate >= {SPEARMAN_MIN:.2f})")
    print(
        f"  flip rate:    {flips.flip_rate:.2%} "
        f"({flips.flip_count}/{flips.item_count}, gate <= {FLIP_RATE_MAX:.0%})"
    )
    print()
    print("Drift (noise-robust: symmetric sampling noise cancels, one-sided bias does not)")
    signed = [float(b) - float(a) for a, b in zip(scores_a, scores_b, strict=True)]
    mean_signed = sum(signed) / len(signed) if signed else 0.0
    admit_a = _admit_count(
        candidates,
        scores_a,
        admission_min_score=admission_min_score,
    )
    admit_b = _admit_count(
        candidates,
        scores_b,
        admission_min_score=admission_min_score,
    )
    print(f"  mean signed delta (B-A): {mean_signed:+.4f}")
    print(
        f"  admitted: arm A {admit_a}/{len(candidates)}, arm B {admit_b}/{len(candidates)} "
        f"(rate delta {(admit_b - admit_a) / len(candidates):+.1%})"
        if candidates
        else "  admitted: n/a"
    )
    per_platform: dict[str, list[float]] = {}
    for candidate, signed_delta in zip(candidates, signed, strict=True):
        per_platform.setdefault(candidate.source_platform or "unknown", []).append(signed_delta)
    print("  per-platform mean signed delta:")
    for platform_key in sorted(per_platform):
        values = per_platform[platform_key]
        print(f"    {platform_key}: {sum(values) / len(values):+.4f} (n={len(values)})")
    print()
    print("Per-strategy flips")
    if flips.per_strategy:
        for strategy, count in flips.per_strategy.items():
            print(f"  {strategy}: {count}")
    else:
        print("  none")

    print()
    print("Top 10 |delta| items")
    for abs_delta, signed_delta, candidate in _top_delta_items(candidates, scores_a, scores_b):
        title = candidate.title.replace("\n", " ").strip()
        if len(title) > 100:
            title = title[:97] + "..."
        print(
            f"  {candidate.candidate_id:>6} "
            f"{candidate.source_strategy or 'default':<14} "
            f"|delta|={abs_delta:.4f} delta={signed_delta:+.4f} "
            f"{title}"
        )

    print()
    print("Gate:", "PASS" if gate_passed else "FAIL")
    return gate_passed


def _print_repeated_report(
    *,
    arm_b: str,
    candidates: Sequence[ReplayCandidate],
    control_pairs: Sequence[ReplayPair],
    treatment_pairs: Sequence[ReplayPair],
    platform: str | None,
    recall_note: str,
) -> tuple[bool, dict[str, float]]:
    gate_passed, gate = relative_gate(control_pairs, treatment_pairs)
    print("\nProfile Diet Repeated Replay")
    print(f"  sample: {len(candidates)}")
    print(f"  repeats: {len(control_pairs)}")
    print(f"  platform: {platform or 'all'}")
    print(f"  arm B: {arm_b}")
    print(f"  source_context: {_REPLAY_SOURCE_CONTEXT}")
    if recall_note:
        print(f"  note: {recall_note}")
    print()
    print("Pairs")
    for pair in [*control_pairs, *treatment_pairs]:
        metrics = pair.metrics
        print(
            f"  {pair.kind:<9} #{pair.repeat}: first={pair.first_arm:<5} "
            f"flip={metrics.flip_rate:.2%} rho={metrics.spearman:.4f} "
            f"admission_delta={metrics.admission_rate_delta:+.2%}"
        )
    print()
    print("Relative gate")
    print(
        "  treatment flip median: "
        f"{gate['treatment_flip_median']:.2%} "
        f"(control envelope <= {gate['control_flip_ceiling']:.2%})"
    )
    print(
        "  treatment Spearman median: "
        f"{gate['treatment_spearman_median']:.4f} "
        f"(control envelope >= {gate['control_spearman_floor']:.4f})"
    )
    print(
        "  treatment admission delta median: "
        f"{gate['treatment_admission_delta_median']:+.2%} "
        f"(floor {gate['admission_delta_floor']:+.2%})"
    )
    print()
    print("Gate:", "PASS" if gate_passed else "FAIL")
    return gate_passed, gate


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pair_payload(
    pair: ReplayPair,
    *,
    candidates: Sequence[ReplayCandidate],
    admission_min_score: float,
) -> dict[str, object]:
    thresholds = [
        _candidate_admission_threshold(candidate, admission_min_score=admission_min_score)
        for candidate in candidates
    ]
    admitted_a = [
        score >= threshold for score, threshold in zip(pair.scores_a, thresholds, strict=True)
    ]
    admitted_b = [
        score >= threshold for score, threshold in zip(pair.scores_b, thresholds, strict=True)
    ]
    return {
        "repeat": pair.repeat,
        "kind": pair.kind,
        "first_arm": pair.first_arm,
        "scores_a": list(pair.scores_a),
        "scores_b": list(pair.scores_b),
        "scores_a_digest": _digest(list(pair.scores_a)),
        "scores_b_digest": _digest(list(pair.scores_b)),
        "admission_thresholds": thresholds,
        "admitted_a": admitted_a,
        "admitted_b": admitted_b,
        "admitted_a_digest": _digest(admitted_a),
        "admitted_b_digest": _digest(admitted_b),
        "metrics": {
            "mean_abs_delta": pair.metrics.mean_abs_delta,
            "p95_abs_delta": pair.metrics.p95_abs_delta,
            "spearman": pair.metrics.spearman,
            "flip_rate": pair.metrics.flip_rate,
            "flip_count": pair.metrics.flip_count,
            "admitted_a": pair.metrics.admitted_a,
            "admitted_b": pair.metrics.admitted_b,
            "admission_rate_delta": pair.metrics.admission_rate_delta,
        },
    }


def _git_metadata() -> dict[str, object]:
    def command(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    return {
        "commit": command("rev-parse", "HEAD"),
        "dirty": bool(command("status", "--porcelain")),
    }


def _write_artifact(
    output_path: Path,
    *,
    args: argparse.Namespace,
    db_path: Path,
    config_path: Path,
    rows: Sequence[Mapping[str, Any]],
    profile_snapshot: ReplayProfileSnapshot,
    negative_examples: Sequence[Mapping[str, object]] | None,
    candidates: Sequence[ReplayCandidate],
    control_pairs: Sequence[ReplayPair],
    treatment_pairs: Sequence[ReplayPair],
    gate_passed: bool,
    gate: Mapping[str, object],
    admission_min_score: float,
    calls: Sequence[Mapping[str, object]],
    route_audit: Mapping[str, object],
    embedding_audit: Mapping[str, object],
    recall_audit: Mapping[str, object],
    body_cap_affected: int,
    production_prefilter_mode: str,
    topic_lifecycle_serialization: bool,
) -> None:
    candidate_payload = [
        {
            "candidate_id": candidate.candidate_id,
            "source_strategy": candidate.source_strategy,
            "source_platform": candidate.source_platform,
            "content_id": candidate.content_id,
            "score_threshold": candidate.score_threshold,
            "status": str(row.get("status") or ""),
        }
        for candidate, row in zip(candidates, rows, strict=True)
    ]
    mix = Counter(
        (
            str(row.get("status") or ""),
            candidate.source_platform or "unknown",
            candidate.source_strategy or "default",
        )
        for candidate, row in zip(candidates, rows, strict=True)
    )
    artifact = {
        "schema_version": 2,
        "created_at": datetime.now(UTC).isoformat(),
        "git": _git_metadata(),
        "arm_b": str(args.arm_b),
        "sample": len(candidates),
        "repeats": int(args.repeats),
        "platform": args.platform,
        "source_context": _REPLAY_SOURCE_CONTEXT,
        "production_context": {
            "eval_prefilter_mode": production_prefilter_mode,
            "topic_lifecycle_serialization": ("on" if topic_lifecycle_serialization else "off"),
        },
        "replay_context": {"eval_prefilter_mode": "off"},
        "config_path_digest": _digest(str(config_path.resolve())),
        "db_path_digest": _digest(str(db_path.resolve())),
        "admission_min_score": admission_min_score,
        "snapshot": {
            "candidate_digest": _digest([dict(row) for row in rows]),
            "candidate_metadata_digest": _digest(candidate_payload),
            "raw_profile_digest": profile_snapshot.raw_digest,
            "effective_profile_digest": profile_snapshot.effective_digest,
            "overrides_present": profile_snapshot.overrides_present,
            "active_speculation_count": profile_snapshot.active_speculation_count,
            "negative_examples_digest": _digest(negative_examples or []),
        },
        "cohort_mix": [
            {
                "status": key[0],
                "platform": key[1],
                "strategy": key[2],
                "count": count,
            }
            for key, count in sorted(mix.items())
        ],
        "candidates": candidate_payload,
        "control_pairs": [
            _pair_payload(
                pair,
                candidates=candidates,
                admission_min_score=admission_min_score,
            )
            for pair in control_pairs
        ],
        "treatment_pairs": [
            _pair_payload(
                pair,
                candidates=candidates,
                admission_min_score=admission_min_score,
            )
            for pair in treatment_pairs
        ],
        "body_cap": {
            "head": BODY_CAP_HEAD,
            "tail": BODY_CAP_TAIL,
            "joiner": BODY_CAP_JOINER,
            "affected_count": body_cap_affected,
        },
        "gate_constants": {
            "flip_rate_max": FLIP_RATE_MAX,
            "spearman_min": SPEARMAN_MIN,
            "relative_admission_shrink_max": _RELATIVE_ADMISSION_SHRINK_MAX,
            "llm_max_tokens": 4096,
            "replay_temperature": 0.0,
            "production_default_temperature": 0.7,
            "batch_size": _DEFAULT_BATCH_SIZE,
            "chunk_timeout_seconds": CHUNK_TIMEOUT_SECONDS,
        },
        "embedding": dict(embedding_audit),
        "recall": dict(recall_audit),
        "routes": dict(route_audit),
        "gate": {"passed": gate_passed, **dict(gate)},
        "llm_calls": [dict(call) for call in calls],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> int:
    from openbiliclaw.config import _default_config_path, load_config
    from openbiliclaw.discovery.engine import (
        _EVALUATION_BODY_TEXT_HEAD_CAP,
        _EVALUATION_BODY_TEXT_TAIL_CAP,
        _evaluation_recall_interests,
    )

    config = load_config(args.config) if args.config else load_config()
    production_prefilter_mode = validate_replay_prefilter_compatibility(config)
    config_path = Path(args.config) if args.config else _default_config_path()
    db_path = Path(args.db) if args.db else _database_path(config)
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")

    model_override = parse_model_override(str(args.arm_b))
    compact_profile = str(args.arm_b) == "compact"
    body_cap = str(args.arm_b) == "body-cap"
    legacy_reason = str(args.arm_b) == "reason-diet"
    if not compact_profile and not body_cap and model_override is None and not legacy_reason:
        raise ValueError(
            "--arm-b must be compact, body-cap, reason-diet, model=<instance-id> (v2), "
            "or model=<provider:model> (legacy)"
        )

    database = _load_read_only_database(db_path)
    cleanup = ExitStack()
    topic_lifecycle_serialization = cleanup.enter_context(
        configured_topic_lifecycle_serialization(config)
    )
    try:
        rows = _fetch_replay_rows(database, sample=int(args.sample), platform=args.platform)
        frozen_rows_digest = _digest([dict(row) for row in rows])

        data_root = db_path.parent
        # Memory/profile inputs come from the deployment data directory.
        config.data_dir = str(data_root)  # type: ignore[attr-defined]
        profile_snapshot = _load_profile_snapshot(data_root)
        profile = profile_snapshot.effective_profile
        frozen_profile_digest = profile_snapshot.effective_digest
        negative_examples = _recent_negative_exemplars(database)
        candidates = [_row_to_replay_candidate(row) for row in rows]
        discovery_cfg = getattr(config, "discovery", None)
        admission_min_score = float(getattr(discovery_cfg, "admission_min_score", 0.60) or 0.60)
        body_cap_affected = body_cap_affected_count(rows)
        eligible_tail_count = len(_evaluation_recall_interests(profile))

        arm_a_service = _DeterministicLLMService(
            _build_llm_service(config, data_root),
            service="arm_a",
        )
        arm_b_service = _DeterministicLLMService(
            _build_llm_service(config, data_root, model_override=model_override),
            service="arm_b",
        )
        recall_audit = ReplayRecallAudit()

        # The run-scoped L2 database lives until every A/A and A/B call has
        # completed, then closes before its temporary directory is removed.
        with run_scoped_embedding_audit(
            config,
            allow_no_embedding=bool(getattr(args, "allow_no_embedding", False)),
        ) as embedding_audit_service:
            embedding_service: object | None = embedding_audit_service
            recall_note = (
                "related_interests recall disabled by explicit degraded replay flag"
                if embedding_service is None
                else ""
            )
            arm_a_engine = _build_engine(
                arm_a_service,
                config,
                compact_profile=False,
                negative_examples=negative_examples,
                legacy_profile=compact_profile,
                embedding_service=None if compact_profile else embedding_service,
                recall_audit=recall_audit,
            )
            arm_b_engine = _build_engine(
                arm_b_service,
                config,
                compact_profile=compact_profile,
                negative_examples=negative_examples,
                legacy_profile=False,
                embedding_service=embedding_service,
                recall_audit=recall_audit,
            )

            async def score_arm_a(
                *,
                pair_kind: str,
                repeat: int,
                logical_run: str,
            ) -> tuple[float, ...]:
                with replay_call_attribution(
                    pair_kind=pair_kind,
                    repeat=repeat,
                    logical_run=logical_run,
                    arm="A",
                ):
                    if legacy_reason:
                        with legacy_reason_prompts():
                            scores = await _score_contents(
                                arm_a_engine,
                                _rows_to_contents(rows),
                                profile,
                                source_context=_REPLAY_SOURCE_CONTEXT,
                            )
                    elif body_cap:
                        with legacy_body_text_prompt_caps():
                            scores = await _score_contents(
                                arm_a_engine,
                                _rows_to_contents(rows),
                                profile,
                                source_context=_REPLAY_SOURCE_CONTEXT,
                            )
                    else:
                        scores = await _score_contents(
                            arm_a_engine,
                            _rows_to_contents(rows),
                            profile,
                            source_context=_REPLAY_SOURCE_CONTEXT,
                        )
                return tuple(scores)

            async def score_arm_b(
                *,
                pair_kind: str,
                repeat: int,
            ) -> tuple[float, ...]:
                with replay_call_attribution(
                    pair_kind=pair_kind,
                    repeat=repeat,
                    logical_run="B",
                    arm="B",
                ):
                    scores = await _score_contents(
                        arm_b_engine,
                        _rows_to_contents(rows),
                        profile,
                        source_context=_REPLAY_SOURCE_CONTEXT,
                    )
                return tuple(scores)

            async def control_pair(repeat_index: int) -> ReplayPair:
                repeat = repeat_index + 1
                scores_a = await score_arm_a(
                    pair_kind="control",
                    repeat=repeat,
                    logical_run="A1",
                )
                scores_b = await score_arm_a(
                    pair_kind="control",
                    repeat=repeat,
                    logical_run="A2",
                )
                return ReplayPair(
                    repeat=repeat,
                    kind="control",
                    first_arm="A",
                    scores_a=scores_a,
                    scores_b=scores_b,
                    metrics=_pair_metrics(
                        candidates,
                        scores_a,
                        scores_b,
                        admission_min_score=admission_min_score,
                    ),
                )

            async def treatment_pair(repeat_index: int) -> ReplayPair:
                repeat = repeat_index + 1
                if repeat_index % 2 == 0:
                    scores_a = await score_arm_a(
                        pair_kind="treatment",
                        repeat=repeat,
                        logical_run="A",
                    )
                    scores_b = await score_arm_b(pair_kind="treatment", repeat=repeat)
                    first_arm = "A"
                else:
                    scores_b = await score_arm_b(pair_kind="treatment", repeat=repeat)
                    scores_a = await score_arm_a(
                        pair_kind="treatment",
                        repeat=repeat,
                        logical_run="A",
                    )
                    first_arm = "B"
                return ReplayPair(
                    repeat=repeat,
                    kind="treatment",
                    first_arm=first_arm,
                    scores_a=scores_a,
                    scores_b=scores_b,
                    metrics=_pair_metrics(
                        candidates,
                        scores_a,
                        scores_b,
                        admission_min_score=admission_min_score,
                    ),
                )

            control_pairs: list[ReplayPair] = []
            treatment_pairs: list[ReplayPair] = []
            for repeat_index in range(int(args.repeats)):
                # Alternate control/treatment order across repeats so gateway
                # drift is not systematically assigned to one pair type.
                if repeat_index % 2 == 0:
                    control_pairs.append(await control_pair(repeat_index))
                    treatment_pairs.append(await treatment_pair(repeat_index))
                else:
                    treatment_pairs.append(await treatment_pair(repeat_index))
                    control_pairs.append(await control_pair(repeat_index))

            quality_passed, quality_gate = _print_repeated_report(
                arm_b=str(args.arm_b),
                candidates=candidates,
                control_pairs=control_pairs,
                treatment_pairs=treatment_pairs,
                platform=args.platform,
                recall_note=recall_note,
            )
            calls = [*arm_a_service.calls, *arm_b_service.calls]
            route_audit = validate_replay_routes(
                calls,
                repeats=int(args.repeats),
                model_override=model_override,
                expected_control_instance=_expected_evaluation_instance(arm_a_service),
                expected_treatment_instance=_expected_evaluation_instance(arm_b_service),
            )
            expected_recall_runs: set[tuple[str, int, str]] = set()
            for repeat in range(1, int(args.repeats) + 1):
                expected_recall_runs.add(("treatment", repeat, "B"))
                if not compact_profile:
                    expected_recall_runs.update(
                        {
                            ("control", repeat, "A1"),
                            ("control", repeat, "A2"),
                            ("treatment", repeat, "A"),
                        }
                    )
            recall_validation = recall_audit.validate(
                expected_runs=expected_recall_runs,
                minimum_batches_per_run=math.ceil(len(rows) / _DEFAULT_BATCH_SIZE),
                expected_candidate_count=len(rows),
            )
            if embedding_audit_service is None:
                embedding_audit: dict[str, object] = {
                    "passed": False,
                    "degraded": True,
                    "namespace": "",
                    "call_count": 0,
                    "successful_call_count": 0,
                    "dimension": 0,
                    "eligible_tail_count": eligible_tail_count,
                    "blocking_reasons": [
                        "embedding disabled; artifact is degraded and not landing evidence"
                    ],
                    "calls": [],
                }
            else:
                embedding_audit = embedding_audit_service.summary(
                    eligible_tail_count=eligible_tail_count,
                    recall_audit=recall_audit,
                    expected_runs=expected_recall_runs,
                )

            blocking_reasons = replay_blocking_reasons(
                quality_passed=quality_passed,
                route_audit=route_audit,
                embedding_audit=embedding_audit,
                recall_audit=recall_validation,
                body_cap=body_cap,
                body_cap_affected=body_cap_affected,
                body_cap_contract_matches=(
                    _EVALUATION_BODY_TEXT_HEAD_CAP == BODY_CAP_HEAD
                    and _EVALUATION_BODY_TEXT_TAIL_CAP == BODY_CAP_TAIL
                ),
                profile_snapshot_stable=(
                    _digest(_profile_digest_payload(profile)) == frozen_profile_digest
                ),
                candidate_snapshot_stable=(
                    _digest([dict(row) for row in rows]) == frozen_rows_digest
                ),
            )
            gate_passed = not blocking_reasons
            gate: dict[str, object] = {
                **quality_gate,
                "quality_passed": quality_passed,
                "route_passed": bool(route_audit.get("passed")),
                "embedding_passed": bool(embedding_audit.get("passed")),
                "recall_passed": bool(recall_validation.get("passed")),
                "body_cap_affected": body_cap_affected,
                "blocking_reasons": blocking_reasons,
            }
            if blocking_reasons:
                print("\nBlocking reasons")
                for reason in blocking_reasons:
                    print(f"  - {reason}")
                print("\nFinal gate: FAIL")
            else:
                print("\nFinal gate: PASS")

            output_path = Path(args.output)
            _write_artifact(
                output_path,
                args=args,
                db_path=db_path,
                config_path=config_path,
                rows=rows,
                profile_snapshot=profile_snapshot,
                negative_examples=negative_examples,
                candidates=candidates,
                control_pairs=control_pairs,
                treatment_pairs=treatment_pairs,
                gate_passed=gate_passed,
                gate=gate,
                admission_min_score=admission_min_score,
                calls=calls,
                route_audit=route_audit,
                embedding_audit=embedding_audit,
                recall_audit=recall_validation,
                body_cap_affected=body_cap_affected,
                production_prefilter_mode=production_prefilter_mode,
                topic_lifecycle_serialization=topic_lifecycle_serialization,
            )
            print(f"Artifact: {output_path}")
            return 0 if gate_passed else 1
    finally:
        try:
            close_database = getattr(database, "close", None)
            if callable(close_database):
                close_database()
        finally:
            cleanup.close()


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _minimum_three(raw: str) -> int:
    value = _positive_int(raw)
    if value < 3:
        raise argparse.ArgumentTypeError("must be at least 3 for the relative gate")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discovery profile-diet A/B replay gate")
    parser.add_argument("--sample", type=_positive_int, default=100, help="Candidate sample size")
    parser.add_argument(
        "--repeats",
        type=_minimum_three,
        default=3,
        help="Repeated A/A and A/B pairs; minimum 3 (default: 3)",
    )
    parser.add_argument(
        "--platform", type=str, default=None, help="Optional source platform filter"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Explicit path to openbiliclaw.db (default: resolve from config data_dir)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Explicit path to config.toml (default: standard config resolution)",
    )
    parser.add_argument(
        "--arm-b",
        required=True,
        help=(
            "Arm B transform: compact, body-cap, reason-diet, model=<instance-id> (v2), "
            "or model=<provider:model> (legacy)"
        ),
    )
    parser.add_argument(
        "--allow-no-embedding",
        action="store_true",
        help=(
            "Allow an explicitly embedding-disabled config to run only as degraded, "
            "non-landing evidence (the final gate still fails)."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Write raw paired scores, snapshot digests, routes, and gate metrics to JSON",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        exit_code = asyncio.run(run(parse_args()))
    except Exception as exc:
        logger.error("profile diet replay failed: %s", exc)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
