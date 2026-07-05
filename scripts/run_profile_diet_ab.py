"""Replay discovery candidates through two evaluation prompt/input arms.

Usage:
    .venv/bin/python scripts/run_profile_diet_ab.py --arm-b compact --sample 100
    .venv/bin/python scripts/run_profile_diet_ab.py --arm-b body-cap --sample 100
    .venv/bin/python scripts/run_profile_diet_ab.py --arm-b model=deepseek:deepseek-chat

For ``--arm-b compact``, arm A forces the legacy full-profile/no-recall prompt
shape and arm B uses current production inputs: compact profile plus per-item
``related_interests`` recall when an embedding service is configured. For
``body-cap`` and ``model=...`` arms, both sides use production profile/recall
shape so the requested arm remains the only intentional difference.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.profile_diet_ab")

FLIP_RATE_MAX = 0.03
SPEARMAN_MIN = 0.95
CHUNK_TIMEOUT_SECONDS = 900.0

BODY_CAP_HEAD = 200
BODY_CAP_TAIL = 100
BODY_CAP_JOINER = "\u2026"

_REPLAY_STATUSES = frozenset({"evaluated", "cached"})
_DEFAULT_BATCH_SIZE = 45


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
    """Parsed ``model=<provider:model>`` arm override."""

    provider: str
    model: str


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
) -> AdmissionFlipSummary:
    """Return per-strategy admission flips using discovery pipeline thresholds."""

    _validate_aligned_scores(scores_a, scores_b)
    if len(candidates) != len(scores_a):
        raise ValueError("candidates and score lists must have the same length")

    thresholds = _default_strategy_thresholds()
    default_threshold = thresholds.get("default", 0.60)
    per_strategy: dict[str, int] = {}
    flip_count = 0
    for candidate, score_a, score_b in zip(candidates, scores_a, scores_b, strict=True):
        strategy = _candidate_strategy(candidate)
        threshold = thresholds.get(strategy, default_threshold)
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

    # Round-robin across (platform, strategy) groups so the gate sample stays
    # mixed instead of collapsing onto whichever wave evaluated most recently.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in selected:
        group_key = (_normalize_platform(row.get("source_platform")), _candidate_strategy(row))
        groups.setdefault(group_key, []).append(row)
    ordered_groups = [groups[key] for key in sorted(groups)]
    mixed: list[dict[str, Any]] = []
    depth = 0
    while len(mixed) < sample_count and any(depth < len(group) for group in ordered_groups):
        for group in ordered_groups:
            if depth < len(group) and len(mixed) < sample_count:
                mixed.append(group[depth])
        depth += 1
    return mixed


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


def parse_model_override(raw_arm: str) -> ModelOverride | None:
    """Parse a ``model=<provider:model>`` arm value, returning None for other arms."""

    if not raw_arm.startswith("model="):
        return None
    value = raw_arm.removeprefix("model=").strip()
    provider, sep, model = value.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not sep or not provider or not model:
        raise ValueError("--arm-b model override must look like model=<provider:model>")
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


def _default_strategy_thresholds() -> dict[str, float]:
    from openbiliclaw.discovery.candidate_pipeline import _default_score_thresholds

    return _default_score_thresholds()


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
    params: list[object] = ["evaluated", "cached"]
    platform_clause = ""
    if platform_filter:
        platform_clause = "AND lower(source_platform) = ?"
        params.append(platform_filter)
    params.append(limit)
    cursor = database.conn.execute(
        f"""
        SELECT *
        FROM discovery_candidates
        WHERE status IN (?, ?)
          {platform_clause}
        ORDER BY COALESCE(evaluated_at, cached_at, last_seen_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    return select_replay_rows(rows, sample=sample, platform=platform)


def _load_current_profile(data_root: Path) -> object:
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.profile import OnionProfile

    memory = MemoryManager(data_root)
    soul_layer = memory.get_layer("soul")
    soul_layer.load()
    if not soul_layer.data:
        raise RuntimeError(
            f"No current soul profile found in {data_root / 'memory' / 'soul.json'}."
        )
    return OnionProfile.from_dict(dict(soul_layer.data))


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

    try:
        registry = build_llm_registry(config)
        return build_embedding_service(config, registry)
    except Exception:
        logger.debug("Failed to build replay embedding service", exc_info=True)
        return None


class _DeterministicLLMService:
    """Force temperature=0 so the replay measures prompt changes, not sampling noise.

    The production evaluator samples at the provider default temperature; an
    A/A control run showed that single-sample noise alone produces ~17% admission
    flips at N=100 — far above the 3% gate. Pinning temperature makes the two
    arms comparable.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def complete_structured_task(self, **kwargs: Any) -> object:
        kwargs["temperature"] = 0.0
        result = self._inner.complete_structured_task(**kwargs)  # type: ignore[attr-defined]
        return await result


def _build_engine(
    llm_service: object,
    config: object,
    *,
    compact_profile: bool,
    negative_examples: list[dict[str, object]] | None,
    legacy_profile: bool,
    embedding_service: object | None,
) -> object:
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
        compact_evaluation_profile_summary,
    )
    from openbiliclaw.discovery.strategies._utils import build_profile_summary

    class ReplayDiscoveryEngine(ContentDiscoveryEngine):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._replay_negative_examples = negative_examples
            self._replay_compact_profile = compact_profile
            self._replay_legacy_profile = legacy_profile
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
            summary = ContentDiscoveryEngine._evaluation_profile_summary(profile)
            if not bool(getattr(self, "_replay_compact_profile", False)):
                return summary
            return compact_evaluation_profile_summary(summary)

        async def _related_interests_for_content(
            self,
            content: object,
            profile: object,
            *,
            top_k: int = 3,
        ) -> list[dict[str, str]]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return []
            return await super()._related_interests_for_content(content, profile, top_k=top_k)

        async def _related_interests_for_batch(
            self,
            contents: Sequence[object],
            profile: object,
            *,
            top_k: int = 3,
        ) -> dict[int, list[dict[str, str]]]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return {}
            return await super()._related_interests_for_batch(contents, profile, top_k=top_k)

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


def _rows_to_contents(rows: Sequence[Mapping[str, Any]], *, body_cap: bool) -> list[Any]:
    from openbiliclaw.discovery.candidate_pool import row_to_discovered_content

    contents = [row_to_discovered_content(dict(row)) for row in rows]
    if not body_cap:
        return contents
    return [
        replace(content, body_text=cap_body_text(str(content.body_text or "")))
        for content in contents
    ]


async def _score_contents(
    engine: object,
    contents: Sequence[Any],
    profile: object,
    *,
    source_context: str,
) -> list[float]:
    if not contents:
        return []
    # Chunk at the batch size (45), not the 90 hard cap: each chunk carries
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
        # Hard deadline per chunk: a stalled provider/gateway must fail the
        # gate run loudly instead of hanging it forever (observed in prod:
        # one stuck upstream session blocked an un-timeboxed call for 2h+).
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
        except TimeoutError as exc:
            raise RuntimeError(
                f"Evaluation chunk timed out after {CHUNK_TIMEOUT_SECONDS}s "
                f"({source_context}, items {start}..{start + len(chunk) - 1}); "
                "check the LLM provider/gateway and rerun."
            ) from exc
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


def _print_report(
    *,
    arm_b: str,
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    platform: str | None,
    recall_note: str = "",
) -> bool:
    delta = score_delta_summary(scores_a, scores_b)
    spearman = spearman_rank_correlation(scores_a, scores_b)
    flips = admission_flip_summary(candidates, scores_a, scores_b)
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
    thresholds = _default_strategy_thresholds()
    default_threshold = thresholds.get("default", 0.60)

    def _admit_count(scores: Sequence[float]) -> int:
        count = 0
        for candidate, score in zip(candidates, scores, strict=True):
            threshold = thresholds.get(_candidate_strategy(candidate), default_threshold)
            if score >= threshold:
                count += 1
        return count

    admit_a = _admit_count(scores_a)
    admit_b = _admit_count(scores_b)
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


async def run(args: argparse.Namespace) -> int:
    from openbiliclaw.config import load_config

    config = load_config(args.config) if args.config else load_config()
    db_path = Path(args.db) if args.db else _database_path(config)
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")

    model_override = parse_model_override(str(args.arm_b))
    compact_profile = str(args.arm_b) == "compact"
    body_cap = str(args.arm_b) == "body-cap"
    if body_cap:
        raise ValueError(
            "--arm-b body-cap is obsolete: body_text head+tail capping became production "
            "behavior for both arms (Task 7); pre-capping again only corrupts the "
            "description-dedup relation and skews the comparison."
        )
    if not compact_profile and model_override is None:
        raise ValueError("--arm-b must be compact or model=<provider:model>")

    database = _load_read_only_database(db_path)
    rows = _fetch_replay_rows(database, sample=int(args.sample), platform=args.platform)
    if not rows:
        raise RuntimeError(
            f"No evaluated/cached discovery candidates found for replay in {db_path} "
            "(use --db to point at the deployment's data/openbiliclaw.db)."
        )

    data_root = db_path.parent
    # Point every data-relative consumer (embedding L2 cache, memory layers)
    # at the deployment's data dir — package-relative resolution breaks in
    # multi-checkout layouts and silently produces cold caches.
    config.data_dir = str(data_root)  # type: ignore[attr-defined]
    profile = _load_current_profile(data_root)
    negative_examples = _recent_negative_exemplars(database)
    candidates = [_row_to_replay_candidate(row) for row in rows]

    arm_a_service = _DeterministicLLMService(_build_llm_service(config, data_root))
    arm_b_service = _DeterministicLLMService(
        _build_llm_service(config, data_root, model_override=model_override)
    )
    embedding_service = _build_embedding_service(config)
    recall_note = (
        "related_interests recall disabled: embedding service unavailable"
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
    )
    arm_b_engine = _build_engine(
        arm_b_service,
        config,
        compact_profile=compact_profile,
        negative_examples=negative_examples,
        legacy_profile=False,
        embedding_service=embedding_service,
    )

    scores_a = await _score_contents(
        arm_a_engine,
        _rows_to_contents(rows, body_cap=False),
        profile,
        source_context="profile_diet_ab.arm_a",
    )
    scores_b = await _score_contents(
        arm_b_engine,
        _rows_to_contents(rows, body_cap=body_cap),
        profile,
        source_context=f"profile_diet_ab.arm_b.{args.arm_b}",
    )

    gate_passed = _print_report(
        arm_b=str(args.arm_b),
        candidates=candidates,
        scores_a=scores_a,
        scores_b=scores_b,
        platform=args.platform,
        recall_note=recall_note,
    )
    return 0 if gate_passed else 1


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discovery profile-diet A/B replay gate")
    parser.add_argument("--sample", type=_positive_int, default=100, help="Candidate sample size")
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
        help="Arm B transform: compact, body-cap, or model=<provider:model>",
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
