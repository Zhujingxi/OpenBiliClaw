from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.config import load_config
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.llm.base import classify_llm_failure_kind
from openbiliclaw.llm.concurrency import LLMConcurrencyGate
from openbiliclaw.llm.registry import build_llm_registry
from openbiliclaw.llm.service import LLMService, module_overrides_from_config
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.config import Config
    from openbiliclaw.llm.base import LLMRegistry

_LIVE = os.getenv("OPENBILICLAW_REFILL_E2E", "") == "1"
_LIVE_CONFIG_ENV = "OPENBILICLAW_REFILL_CONFIG"
_LIVE_PROVIDER_ENV = "OPENBILICLAW_REFILL_PROVIDER"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _LIVE, reason="set OPENBILICLAW_REFILL_E2E=1 for live refill E2E"),
]


def _load_live_config_and_registry() -> tuple[Config, LLMRegistry]:
    """Load an opt-in live-test config without changing persisted settings.

    The optional environment controls exist only for this explicitly enabled
    integration test. They let an operator choose a known-good configured
    provider while keeping normal config loading and all on-disk settings
    untouched.
    """

    config_path = os.getenv(_LIVE_CONFIG_ENV, "").strip()
    config = load_config(config_path) if config_path else load_config()
    requested_provider = os.getenv(_LIVE_PROVIDER_ENV, "").strip().lower()
    if requested_provider:
        config = replace(
            config,
            llm=replace(config.llm, default_provider=requested_provider),
        )
    registry = build_llm_registry(config)
    if requested_provider and registry.default_provider != requested_provider:
        raise RuntimeError("Requested live refill provider is unavailable.")
    return config, registry


def _profile() -> SoulProfile:
    return SoulProfile(
        core_traits=["curious"],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="software engineering", category="technology", weight=0.9)]
        ),
    )


@dataclass
class _LiveMetrics:
    gate: LLMConcurrencyGate
    peak_total: int = 0
    peak_background: int = 0
    provider_round_count: int = 0
    transient_retry_count: int = 0
    transient_registry_failures: int = 0
    retry_pending: set[str] = field(default_factory=set)
    expression_batch_sizes: list[int] = field(default_factory=list)
    provider_active: int = 0
    peak_provider_active: int = 0
    expression_active: int = 0
    peak_expression_active: int = 0

    def observe_gate(self) -> None:
        status = self.gate.status_payload()
        self.peak_total = max(self.peak_total, int(status["llm_total_active"]))
        self.peak_background = max(self.peak_background, int(status["llm_background_active"]))


class _MonitoredRegistry:
    def __init__(self, registry: Any, metrics: _LiveMetrics) -> None:
        self.registry = registry
        self.metrics = metrics
        self.default_provider = registry.default_provider

    def is_chat_capable(self, name: str) -> bool:
        return bool(self.registry.is_chat_capable(name))

    async def complete(self, messages: list[dict[str, str]], **kwargs: object) -> object:
        return await self._call("complete", None, messages, kwargs)

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> object:
        return await self._call("complete_provider", provider_name, messages, kwargs)

    async def _call(
        self,
        method: str,
        provider_name: str | None,
        messages: list[dict[str, str]],
        kwargs: dict[str, object],
    ) -> object:
        self.metrics.observe_gate()
        logical_caller = self._logical_caller(messages)
        request_fingerprint = self._request_fingerprint(
            logical_caller=logical_caller,
            method=method,
            provider_name=provider_name,
            messages=messages,
            kwargs=kwargs,
        )
        self.metrics.provider_round_count += 1
        if request_fingerprint in self.metrics.retry_pending:
            self.metrics.retry_pending.remove(request_fingerprint)
            self.metrics.transient_retry_count += 1
        self.metrics.provider_active += 1
        self.metrics.peak_provider_active = max(
            self.metrics.peak_provider_active, self.metrics.provider_active
        )
        is_expression = '"expression"' in messages[0]["content"]
        if is_expression:
            self.metrics.expression_active += 1
            self.metrics.peak_expression_active = max(
                self.metrics.peak_expression_active, self.metrics.expression_active
            )
            match = re.search(
                r"<content_batch>\s*(.*?)\s*</content_batch>", messages[-1]["content"], re.S
            )
            if match is not None:
                value = json.loads(match.group(1))
                if isinstance(value, list):
                    self.metrics.expression_batch_sizes.append(len(value))
        try:
            if method == "complete_provider":
                assert provider_name is not None
                return await self.registry.complete_provider(provider_name, messages, **kwargs)
            return await self.registry.complete(messages, **kwargs)
        except Exception as exc:
            if classify_llm_failure_kind(exc) in {
                "rate_limited",
                "timeout",
                "connection",
                "server_error",
            }:
                self.metrics.transient_registry_failures += 1
                self.metrics.retry_pending.add(request_fingerprint)
            raise
        finally:
            if is_expression:
                self.metrics.expression_active -= 1
            self.metrics.provider_active -= 1

    @staticmethod
    def _logical_caller(messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"] if messages else ""
        if '"expression"' in system:
            return "refill.expression"
        if '"score"' in system:
            return "refill.evaluation"
        return "interactive_or_other"

    @staticmethod
    def _request_fingerprint(
        *,
        logical_caller: str,
        method: str,
        provider_name: str | None,
        messages: list[dict[str, str]],
        kwargs: dict[str, object],
    ) -> str:
        """Hash one exact logical request without retaining or logging its content."""

        structured_params = {
            key: value
            for key, value in kwargs.items()
            if key
            in {
                "temperature",
                "max_tokens",
                "json_mode",
                "reasoning_effort",
                "model",
            }
        }
        canonical = json.dumps(
            {
                "caller": logical_caller,
                "method": method,
                "provider": provider_name or "",
                "messages": messages,
                "params": structured_params,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda value: f"<{type(value).__name__}>",
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _BarrierRegistry:
    def __init__(
        self, registry: _MonitoredRegistry, metrics: _LiveMetrics, expected: int = 3
    ) -> None:
        self.registry = registry
        self.metrics = metrics
        self.default_provider = registry.default_provider
        self.expected = expected
        self.entered = 0
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    def is_chat_capable(self, name: str) -> bool:
        return bool(self.registry.is_chat_capable(name))

    async def complete(self, messages: list[dict[str, str]], **kwargs: object) -> object:
        self.metrics.observe_gate()
        self.entered += 1
        if self.entered >= self.expected:
            self.ready.set()
        await self.release.wait()
        return await self.registry.complete(messages, **kwargs)

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> object:
        self.metrics.observe_gate()
        self.entered += 1
        if self.entered >= self.expected:
            self.ready.set()
        await self.release.wait()
        return await self.registry.complete_provider(provider_name, messages, **kwargs)


@pytest.mark.asyncio
async def test_real_provider_refill_and_interactive_fourth_slot(tmp_path: Path) -> None:
    print("live_refill_phase=config", flush=True)
    config, registry = _load_live_config_and_registry()
    db = Database(tmp_path / "live-refill.db")
    db.initialize()
    memory = MemoryManager(tmp_path / "memory", database=db)
    gate = LLMConcurrencyGate(total_concurrency=4)
    gate.update_inventory(available=0, target=8)
    metrics = _LiveMetrics(gate)
    monitored_registry = _MonitoredRegistry(registry, metrics)
    overrides = module_overrides_from_config(config)
    service = LLMService(
        registry=monitored_registry,
        memory=memory,
        concurrency=4,
        concurrency_gate=gate,
        module_overrides=overrides,
    )
    profile = _profile()

    client = BilibiliAPIClient(cookie="")
    try:
        async with asyncio.timeout(30):
            rows = (await client.get_ranking())[:8]
    finally:
        await client.close()
    items = [
        DiscoveredContent(
            bvid=str(row.get("bvid", "")),
            content_id=str(row.get("bvid", "")),
            source_platform="bilibili",
            source_strategy="trending",
            title=str(row.get("title", "")),
            up_name=str((row.get("owner") or {}).get("name", "")),
            description=str(row.get("desc", "")),
            view_count=int((row.get("stat") or {}).get("view", 0) or 0),
            like_count=int((row.get("stat") or {}).get("like", 0) or 0),
        )
        for row in rows
        if str(row.get("bvid", ""))
    ]
    print(f"live_refill_phase=ranking fetched={len(items)}", flush=True)
    assert items, "live fetched_count=0"
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=service, database=db),
        pool_target_count=8,
    )
    assert pipeline.enqueue_candidates(items) > 0
    claim = pipeline.claim_batch(limit=8)
    assert claim is not None
    print("live_refill_phase=evaluation_start", flush=True)
    async with asyncio.timeout(180):
        outcome = await pipeline.evaluate_claim(claim, profile)
    passing_scores = sum(
        score >= pipeline._threshold_for(row)
        for row, score in zip(claim.rows, outcome.scores, strict=True)
    )
    parser_unresolved = sum(
        item.relevance_reason == "evaluation_response_missing" for item in claim.items
    )
    result = await pipeline.complete_claim(outcome, admission_limit=8)
    print(
        f"live_refill_phase=evaluation_done evaluated={result['evaluated']} "
        f"passing_scores={passing_scores} parser_unresolved={parser_unresolved} "
        f"admitted={result['cached']} "
        f"rejected={result['rejected']}",
        flush=True,
    )
    assert result["evaluated"] > 0
    assert result["cached"] > 0, (
        f"live evaluated={result['evaluated']} passing_scores={passing_scores} "
        f"parser_unresolved={parser_unresolved} "
        f"admitted=0 rejected={result['rejected']}"
    )
    recommendation = RecommendationEngine(llm=service, database=db, expression_batch_concurrency=2)
    print("live_refill_phase=copy_start", flush=True)
    async with asyncio.timeout(180):
        copied = await recommendation._drain_expression_copy(
            profile=profile, limit=8, batch_size=30
        )
    print(f"live_refill_phase=copy_done copied={copied}", flush=True)
    assert copied > 0
    before = db.count_pool_candidates()
    assert before > 0
    maintained = db.maintain_pool_inventory(
        target=8,
        raw_ceiling=16,
        source_share_quotas={"bilibili": 8},
    )
    assert maintained.available_after >= min(before, 8)
    print(
        f"live_refill_phase=maintenance before={before} after={maintained.available_after}",
        flush=True,
    )

    barrier = _BarrierRegistry(monitored_registry, metrics)
    background = LLMService(
        registry=barrier,
        memory=memory,
        concurrency=4,
        concurrency_gate=gate,
        module_overrides=overrides,
    )
    interactive = LLMService(
        registry=monitored_registry,
        memory=memory,
        concurrency=4,
        concurrency_gate=gate,
        module_overrides=overrides,
    )
    background_tasks = [
        asyncio.create_task(
            background.complete_structured_task(
                system_instruction="Return a short JSON object.",
                user_input='Return {"ok": true}.',
                caller="discovery.evaluate_batch",
                max_tokens=32,
            )
        )
        for _ in range(3)
    ]
    print("live_refill_phase=interactive_barrier_start", flush=True)
    await asyncio.wait_for(barrier.ready.wait(), timeout=10)
    assert gate.status_payload()["llm_background_active"] == 3
    interactive_task = asyncio.create_task(
        interactive.complete_structured_task(
            system_instruction="Reply briefly.",
            user_input="Say OK.",
            caller="soul.dialogue",
            max_tokens=16,
        )
    )
    async with asyncio.timeout(10):
        while gate.status_payload()["llm_total_active"] < 4:
            await asyncio.sleep(0.01)
    print("live_refill_phase=interactive_fourth_entered", flush=True)
    barrier.release.set()
    async with asyncio.timeout(180):
        await asyncio.gather(*background_tasks, interactive_task)
    print("live_refill_phase=interactive_done", flush=True)
    status = gate.status_payload()
    assert status["llm_total_active"] == 0
    assert status["llm_background_active"] == 0
    assert metrics.peak_total <= 4
    assert metrics.peak_background <= 3
    assert metrics.peak_total == 4
    assert max(metrics.expression_batch_sizes) <= 30
    assert metrics.peak_expression_active <= 2
    assert metrics.transient_retry_count <= metrics.provider_round_count
    print(
        "live_refill_summary "
        f"fetched={len(items)} evaluated={result['evaluated']} copied={copied} "
        f"available_before={before} available_after={maintained.available_after} "
        f"peak_total={metrics.peak_total} peak_background={metrics.peak_background} "
        f"max_copy_batch={max(metrics.expression_batch_sizes)} "
        f"copy_fanout={metrics.peak_expression_active} "
        f"provider_round_count={metrics.provider_round_count} "
        f"transient_retry_count={metrics.transient_retry_count} "
        f"transient_registry_failures={metrics.transient_registry_failures}"
    )
