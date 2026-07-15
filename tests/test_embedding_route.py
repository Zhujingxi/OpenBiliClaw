"""Ordered embedding route tests.

All adapters are local fakes.  The suite must never contact a model service,
local Ollama daemon, or credential store.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast

import pytest

from openbiliclaw.llm.base import LLMProviderError
from openbiliclaw.llm.embedding import EmbeddingCache, EmbeddingService
from openbiliclaw.llm.embedding_route import (
    FIXED_IMAGE_PROBE_PNG,
    EmbeddingRouteExhaustedError,
    OrderedEmbeddingRoute,
)
from openbiliclaw.llm.route import CircuitTable
from openbiliclaw.model_config import (
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _Call:
    provider_id: str
    operation: str
    settings: EmbeddingModelSettings
    payload: object
    mime_type: str = ""


@dataclass
class _FakeEmbeddingAdapter:
    name: str
    settings: EmbeddingModelSettings
    outcomes: list[object]
    image_outcomes: list[object] = field(default_factory=list)
    connection_type: str = "openai_compatible"
    preset: str = "custom"
    supports_image_embedding: bool = False
    calls: list[_Call] = field(default_factory=list)

    async def embed(self, text: str) -> list[float]:
        self.calls.append(_Call(self.name, "text", self.settings, text))
        return cast("list[float]", self._next(self.outcomes))

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
    ) -> list[float]:
        self.calls.append(_Call(self.name, "image", self.settings, image_bytes, mime_type))
        return cast("list[float]", self._next(self.image_outcomes))

    @staticmethod
    def _next(outcomes: list[object]) -> object:
        if not outcomes:
            raise AssertionError("fake adapter called more times than expected")
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass
class _RetryingAdapter(_FakeEmbeddingAdapter):
    events: list[str] = field(default_factory=list)

    async def embed(self, text: str) -> list[float]:
        self.calls.append(_Call(self.name, "text", self.settings, text))
        while self.outcomes:
            self.events.append(f"{self.name}:transport-attempt")
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, TimeoutError) and self.outcomes:
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            return cast("list[float]", outcome)
        raise AssertionError("fake adapter called more times than expected")


def _settings(
    *,
    model: str = "bge-m3",
    dims: int = 2,
    threshold: float = 0.82,
    multimodal: bool = False,
) -> EmbeddingModelSettings:
    return EmbeddingModelSettings(
        model=model,
        output_dimensionality=dims,
        similarity_threshold=threshold,
        multimodal_enabled=multimodal,
    )


def _adapter(
    provider_id: str,
    settings: EmbeddingModelSettings,
    *outcomes: object,
    images: tuple[object, ...] = (),
    connection_type: str = "openai_compatible",
    supports_image: bool = False,
) -> _FakeEmbeddingAdapter:
    return _FakeEmbeddingAdapter(
        name=provider_id,
        settings=settings,
        outcomes=list(outcomes),
        image_outcomes=list(images),
        connection_type=connection_type,
        supports_image_embedding=supports_image,
    )


async def test_route_preserves_exact_order_and_same_type_stable_ids() -> None:
    settings = _settings()
    calls: list[str] = []
    providers = (
        _adapter("endpoint-a", settings, []),
        _adapter("endpoint-b", settings, ["not-numeric", 1.0]),
        _adapter("endpoint-c", settings, [0.25, 0.75]),
    )
    for provider in providers:
        original = provider.embed

        async def record(text: str, *, _provider: Any = provider, _call: Any = original) -> Any:
            calls.append(_provider.name)
            return await _call(text)

        provider.embed = record

    route = OrderedEmbeddingRoute(providers, settings=settings, revision="revision-1")

    assert await route.embed("hello") == [0.25, 0.75]
    assert calls == ["endpoint-a", "endpoint-b", "endpoint-c"]
    assert [provider.connection_type for provider in route.providers] == [
        "openai_compatible",
        "openai_compatible",
        "openai_compatible",
    ]
    assert [provider.name for provider in route.providers] == [
        "endpoint-a",
        "endpoint-b",
        "endpoint-c",
    ]


async def test_every_provider_call_uses_the_identical_shared_settings_object() -> None:
    settings = _settings()
    first = _adapter("first", settings, [])
    second = _adapter("second", settings, [1.0, 0.0])
    route = OrderedEmbeddingRoute((first, second), settings=settings, revision="r1")

    assert await route.embed("text") == [1.0, 0.0]
    assert first.calls[0].settings is settings
    assert second.calls[0].settings is settings
    assert first.settings is second.settings is route.settings


async def test_route_rejects_per_call_model_override() -> None:
    settings = _settings(model="shared-only")
    provider = _adapter("provider", settings, [1.0, 0.0])
    route = OrderedEmbeddingRoute((provider,), settings=settings, revision="r1")

    with pytest.raises(ValueError, match="model override is not allowed"):
        await route.embed("text", model="provider-specific-override")

    assert provider.calls == []


def test_route_rejects_adapter_bound_to_a_distinct_settings_object() -> None:
    settings = _settings()
    equal_but_distinct = replace(settings)

    with pytest.raises(ValueError, match="shared settings object"):
        OrderedEmbeddingRoute(
            (_adapter("wrong-space", equal_but_distinct, [1.0, 0.0]),),
            settings=settings,
            revision="r1",
        )


async def test_provider_transport_retry_finishes_before_route_fallback() -> None:
    settings = _settings()
    events: list[str] = []
    first = _RetryingAdapter(
        name="first",
        settings=settings,
        outcomes=[TimeoutError("retry me"), []],
        events=events,
    )
    second = _RetryingAdapter(
        name="second",
        settings=settings,
        outcomes=[[1.0, 0.0]],
        events=events,
    )
    route = OrderedEmbeddingRoute((first, second), settings=settings, revision="r1")

    assert await route.embed("text") == [1.0, 0.0]
    assert events == [
        "first:transport-attempt",
        "first:transport-attempt",
        "second:transport-attempt",
    ]


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        [1.0, "not-numeric"],
        [True, 0.0],
        [math.nan, 0.0],
        [math.inf, 0.0],
        [10**10000, 0.0],
        (1.0, 0.0),
    ],
)
async def test_empty_non_numeric_and_non_finite_vectors_fall_back_without_circuit(
    invalid: object,
) -> None:
    settings = _settings()
    first = _adapter("invalid", settings, invalid)
    second = _adapter("healthy", settings, [0.0, 1.0])
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (first, second),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    assert await route.embed("text") == [0.0, 1.0]
    assert circuits.state_for("invalid", "r1") is None


async def test_dimension_mismatch_opens_permanent_revision_scoped_config_circuit() -> None:
    settings = _settings(dims=2)
    first = _adapter("same-id", settings, [1.0], [1.0, 0.0])
    fallback = _adapter("fallback", settings, [0.0, 1.0], [0.0, 1.0])
    circuits = CircuitTable()
    old_route = OrderedEmbeddingRoute(
        (first, fallback),
        settings=settings,
        revision="old-revision",
        circuits=circuits,
    )

    assert await old_route.embed("first call") == [0.0, 1.0]
    state = circuits.state_for("same-id", "old-revision")
    assert state is not None
    assert state.failure_kind == "config_error"
    assert state.permanent is True

    assert await old_route.embed("second call") == [0.0, 1.0]
    assert len(first.calls) == 1

    new_route = OrderedEmbeddingRoute(
        (first, fallback),
        settings=settings,
        revision="new-revision",
        circuits=circuits,
    )
    assert await new_route.embed("new revision") == [1.0, 0.0]
    assert len(first.calls) == 2
    assert circuits.state_for("same-id", "old-revision") is state
    assert circuits.state_for("same-id", "new-revision") is None


async def test_inflight_normal_success_cannot_clear_concurrent_config_circuit() -> None:
    settings = _settings(dims=2)
    started = asyncio.Event()
    release = asyncio.Event()

    class ConcurrentAdapter(_FakeEmbeddingAdapter):
        async def embed(self, text: str) -> list[float]:
            self.calls.append(_Call(self.name, "text", self.settings, text))
            if text == "slow valid":
                started.set()
                await release.wait()
                return [1.0, 0.0]
            return [1.0]

    provider = ConcurrentAdapter("same-id", settings, outcomes=[])
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (provider,),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    slow_success = asyncio.create_task(route.embed("slow valid"))
    await started.wait()
    with pytest.raises(EmbeddingRouteExhaustedError):
        await route.embed("dimension mismatch")
    permanent = circuits.state_for("same-id", "r1")
    assert permanent is not None and permanent.permanent

    release.set()
    assert await slow_success == [1.0, 0.0]
    assert circuits.state_for("same-id", "r1") is permanent


async def test_enabled_multimodal_route_skips_provider_without_image_capability() -> None:
    settings = _settings(multimodal=True)
    text_only = _adapter("text-only", settings, [1.0, 0.0])
    multimodal = _adapter(
        "multimodal",
        settings,
        [0.0, 1.0],
        images=([0.0, 1.0],),
        supports_image=True,
    )
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (text_only, multimodal),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    assert await route.embed("text") == [0.0, 1.0]
    assert text_only.calls == []
    assert circuits.state_for("text-only", "r1") is None


async def test_embed_image_falls_back_and_uses_shared_vector_space() -> None:
    settings = _settings(multimodal=True)
    empty = _adapter(
        "empty-image",
        settings,
        [1.0, 0.0],
        images=([],),
        supports_image=True,
    )
    valid = _adapter(
        "valid-image",
        settings,
        [1.0, 0.0],
        images=([0.4, 0.6],),
        supports_image=True,
    )
    route = OrderedEmbeddingRoute((empty, valid), settings=settings, revision="r1")

    assert await route.embed_image(b"image", mime_type="image/webp") == [0.4, 0.6]
    assert [call.provider_id for call in (*empty.calls, *valid.calls)] == [
        "empty-image",
        "valid-image",
    ]
    assert all(call.settings is settings for call in (*empty.calls, *valid.calls))


async def test_image_dimension_mismatch_opens_the_same_config_circuit() -> None:
    settings = _settings(multimodal=True)
    mismatch = _adapter(
        "mismatch",
        settings,
        [1.0, 0.0],
        images=([1.0],),
        supports_image=True,
    )
    healthy = _adapter(
        "healthy",
        settings,
        [1.0, 0.0],
        images=([0.25, 0.75],),
        supports_image=True,
    )
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (mismatch, healthy),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    assert await route.embed_image(b"image/png", mime_type="image/png") == [0.25, 0.75]
    state = circuits.state_for("mismatch", "r1")
    assert state is not None
    assert state.failure_kind == "config_error"
    assert state.permanent is True


async def test_exact_probe_bypasses_config_circuit_calls_only_target_and_uses_fixed_png(
    tmp_path: Path,
) -> None:
    settings = _settings(multimodal=True)
    target = _adapter(
        "target",
        settings,
        [1.0],
        [0.7, 0.3],
        images=([0.6, 0.4],),
        supports_image=True,
    )
    fallback = _adapter(
        "fallback",
        settings,
        [0.0, 1.0],
        images=([0.0, 1.0],),
        supports_image=True,
    )
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (target, fallback),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    service = EmbeddingService(route, persistent_cache=cache)

    assert await service.embed("opens target circuit") == [0.0, 1.0]
    assert circuits.should_skip("target", "r1") is True
    fallback_calls_before_probe = len(fallback.calls)
    cache_count_before_probe = cache.count()
    circuits.record_failure(
        "target",
        "other-revision",
        "config_error",
        LLMProviderError("fixed test failure"),
    )
    circuits.record_failure(
        "fallback",
        "r1",
        "config_error",
        LLMProviderError("fixed test failure"),
    )
    target_other_revision = circuits.state_for("target", "other-revision")
    fallback_same_revision = circuits.state_for("fallback", "r1")

    result = await route.probe_provider("target")

    assert dataclasses.asdict(result) == {
        "provider_id": "target",
        "observed_dimension": 2,
        "image_probe_performed": True,
    }
    assert len(fallback.calls) == fallback_calls_before_probe
    assert target.calls[-2].operation == "text"
    assert target.calls[-1] == _Call(
        "target",
        "image",
        settings,
        FIXED_IMAGE_PROBE_PNG,
        "image/png",
    )
    assert FIXED_IMAGE_PROBE_PNG.startswith(b"\x89PNG\r\n\x1a\n")
    assert circuits.state_for("target", "r1") is None
    assert circuits.state_for("target", "other-revision") is target_other_revision
    assert circuits.state_for("fallback", "r1") is fallback_same_revision
    assert cache.count() == cache_count_before_probe


async def test_probe_accepts_native_dimension_zero_and_reports_observed_dimension() -> None:
    settings = _settings(dims=0)
    provider = _adapter("native-dimension", settings, [0.1, 0.2, 0.3])
    route = OrderedEmbeddingRoute((provider,), settings=settings, revision="r1")

    result = await route.probe_provider("native-dimension")

    assert result.observed_dimension == 3
    assert result.image_probe_performed is False


async def test_multimodal_native_dimension_probe_rejects_text_image_length_mismatch() -> None:
    settings = _settings(dims=0, multimodal=True)
    provider = _adapter(
        "native-mismatch",
        settings,
        [0.1, 0.2, 0.3],
        images=([0.4, 0.6],),
        supports_image=True,
    )
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (provider,),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    with pytest.raises(EmbeddingRouteExhaustedError) as captured:
        await route.probe_provider("native-mismatch")

    assert [attempt.failure_kind for attempt in captured.value.attempts] == ["config_error"]
    state = circuits.state_for("native-mismatch", "r1")
    assert state is not None
    assert state.failure_kind == "config_error"
    assert state.permanent is True
    assert [call.operation for call in provider.calls] == ["text", "image"]


async def test_multimodal_native_dimension_probe_accepts_matching_lengths() -> None:
    settings = _settings(dims=0, multimodal=True)
    provider = _adapter(
        "native-match",
        settings,
        [0.1, 0.2, 0.3],
        images=([0.4, 0.5, 0.6],),
        supports_image=True,
    )
    route = OrderedEmbeddingRoute((provider,), settings=settings, revision="r1")

    result = await route.probe_provider("native-match")

    assert result.observed_dimension == 3
    assert result.image_probe_performed is True
    assert route.circuits.state_for("native-match", "r1") is None


async def test_failed_exact_probe_does_not_weaken_permanent_config_circuit() -> None:
    settings = _settings()
    target = _adapter(
        "target",
        settings,
        [1.0],
        TimeoutError("upstream secret: bearer-sensitive-value"),
    )
    fallback = _adapter("fallback", settings, [0.0, 1.0])
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (target, fallback),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    assert await route.embed("open circuit") == [0.0, 1.0]
    before = circuits.state_for("target", "r1")
    assert before is not None and before.permanent

    with pytest.raises(EmbeddingRouteExhaustedError):
        await route.probe_provider("target")

    assert circuits.state_for("target", "r1") is before


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        ValueError("request schema is invalid"),
        TypeError("programming error"),
        RuntimeError("internal invariant failed"),
    ],
)
async def test_non_provider_failures_propagate_without_fallback_or_circuit(
    error: BaseException,
) -> None:
    settings = _settings()
    first = _adapter("first", settings, error)
    fallback = _adapter("fallback", settings, [0.0, 1.0])
    circuits = CircuitTable()
    route = OrderedEmbeddingRoute(
        (first, fallback),
        settings=settings,
        revision="r1",
        circuits=circuits,
    )

    with pytest.raises(
        type(error),
        match=None if isinstance(error, asyncio.CancelledError) else str(error),
    ):
        await route.embed("private request")

    assert len(first.calls) == 1
    assert fallback.calls == []
    assert circuits.state_for("first", "r1") is None


async def test_all_provider_failure_is_structured_and_secret_safe() -> None:
    sentinel = "Bearer sensitive-upstream-body"
    settings = _settings()
    first = _adapter("first", settings, LLMProviderError(sentinel))
    second = _adapter("second", settings, [])
    route = OrderedEmbeddingRoute((first, second), settings=settings, revision="r1")

    with pytest.raises(EmbeddingRouteExhaustedError) as captured:
        await route.embed("text containing private user data")

    error = captured.value
    assert [attempt.provider_id for attempt in error.attempts] == ["first", "second"]
    assert [attempt.failure_kind for attempt in error.attempts] == [
        "provider_error",
        "empty_vector",
    ]
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert "private user data" not in str(error)
    assert all(sentinel not in repr(attempt) for attempt in error.attempts)


def test_cache_namespace_ignores_provider_order_and_identity() -> None:
    settings = _settings(model="shared-model", dims=1024, multimodal=True)
    providers_ab = (
        _adapter("endpoint-a", settings, [0.0] * 1024, supports_image=True),
        _adapter("endpoint-b", settings, [0.0] * 1024, supports_image=True),
    )
    providers_ba = tuple(reversed(providers_ab))
    route_ab = OrderedEmbeddingRoute(providers_ab, settings=settings, revision="r1")
    route_ba = OrderedEmbeddingRoute(providers_ba, settings=settings, revision="r1")
    route_other_id = OrderedEmbeddingRoute(
        (_adapter("unrelated-id", settings, [0.0] * 1024, supports_image=True),),
        settings=settings,
        revision="r1",
    )

    namespaces = {
        EmbeddingService(route_ab)._cache_model,
        EmbeddingService(route_ba)._cache_model,
        EmbeddingService(route_other_id)._cache_model,
        settings.cache_namespace(),
    }
    assert len(namespaces) == 1


@pytest.mark.parametrize(
    "changed",
    [
        _settings(model="different-model"),
        _settings(dims=3),
        _settings(threshold=0.75),
        _settings(multimodal=True),
    ],
)
def test_cache_namespace_changes_for_every_shared_setting(
    changed: EmbeddingModelSettings,
) -> None:
    assert _settings().cache_namespace() != changed.cache_namespace()


def test_registry_builds_native_route_in_exact_order_with_shared_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import registry as registry_module

    settings = _settings()
    records = (
        EmbeddingProviderConfig(
            id="same-type-a",
            name="Endpoint A",
            type="openai_compatible",
            preset="custom",
            base_url="https://a.example.test/v1",
            credential=CredentialConfig(source="inline", value="secret-a"),
        ),
        EmbeddingProviderConfig(
            id="same-type-b",
            name="Endpoint B",
            type="openai_compatible",
            preset="custom",
            base_url="https://b.example.test/v1",
            credential=CredentialConfig(source="inline", value="secret-b"),
        ),
    )
    built: list[tuple[str, EmbeddingModelSettings]] = []

    def fake_build(record: EmbeddingProviderConfig, shared: EmbeddingModelSettings, _: Any) -> Any:
        built.append((record.id, shared))
        return _adapter(record.id, shared, [1.0, 0.0])

    monkeypatch.setattr(registry_module, "build_embedding_adapter", fake_build)
    service = registry_module.build_ordered_embedding_service(
        EmbeddingRouteConfig(enabled=True, settings=settings, providers=records),
        revision="r1",
        runtime_options=registry_module.AdapterRuntimeOptions(),
    )

    assert service is not None
    assert [provider.name for provider in service._provider.providers] == [  # noqa: SLF001
        "same-type-a",
        "same-type-b",
    ]
    assert [provider_id for provider_id, _ in built] == ["same-type-a", "same-type-b"]
    assert all(shared is settings for _, shared in built)
    assert service._cache_model == settings.cache_namespace()  # noqa: SLF001


def test_registry_keeps_disabled_native_embedding_route_disabled() -> None:
    from openbiliclaw.llm import registry as registry_module

    assert (
        registry_module.build_ordered_embedding_service(
            EmbeddingRouteConfig(enabled=False, settings=_settings(), providers=()),
            revision="r1",
            runtime_options=registry_module.AdapterRuntimeOptions(),
        )
        is None
    )
