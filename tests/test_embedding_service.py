"""Tests for embedding cache and service helpers."""

import asyncio
from pathlib import Path

import pytest

from openbiliclaw.llm.base import LLMProviderError
from openbiliclaw.llm.embedding import (
    EmbeddingCache,
    EmbeddingService,
    image_embedding_cache_key,
)
from openbiliclaw.llm.embedding_route import OrderedEmbeddingRoute
from openbiliclaw.llm.gemini_provider import GeminiProvider
from openbiliclaw.model_config import EmbeddingModelSettings


class _FakeEmbedProvider:
    """Minimal ``SupportsEmbed`` double with controllable behaviour."""

    def __init__(
        self, *, vector: list[float] | None = None, error: BaseException | None = None
    ) -> None:
        self._vector = [0.1, 0.2, 0.3] if vector is None else vector
        self._error = error
        self.calls: list[str] = []

    async def embed(self, text: str, *, model: str = "") -> list[float]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return list(self._vector)


class _FakeImageEmbedProvider(_FakeEmbedProvider):
    """Text + image embedding double for multimodal path tests."""

    supports_image_embedding = True

    def __init__(
        self,
        *,
        vector: list[float] | None = None,
        image_vector: list[float] | None = None,
        error: BaseException | None = None,
        image_error: BaseException | None = None,
    ) -> None:
        super().__init__(vector=vector, error=error)
        self._image_vector = [0.9, 0.1, 0.0] if image_vector is None else image_vector
        self._image_error = image_error
        self.image_calls: list[tuple[int, str, str]] = []

    @staticmethod
    def is_multimodal_embedding_model(model: str) -> bool:
        return "embedding-2" in (model or "").lower()

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        model: str = "",
    ) -> list[float]:
        self.image_calls.append((len(image_bytes), mime_type, model))
        if self._image_error is not None:
            raise self._image_error
        return list(self._image_vector)


async def test_probe_true_when_provider_returns_vector() -> None:
    provider = _FakeEmbedProvider(vector=[0.1, 0.2])
    service = EmbeddingService(provider, model="bge-m3")

    assert await service.probe() is True
    assert provider.calls  # the provider was actually hit


async def test_probe_false_when_provider_returns_empty() -> None:
    # Empty vector = transient/upstream failure (e.g. bge-m3 not pulled).
    provider = _FakeEmbedProvider(vector=[])
    service = EmbeddingService(provider, model="bge-m3")

    assert await service.probe() is False


async def test_probe_false_when_provider_raises() -> None:
    provider = _FakeEmbedProvider(error=LLMProviderError("configured model not found"))
    service = EmbeddingService(provider, model="bge-m3")

    assert await service.probe() is False


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        ValueError("request schema is invalid"),
        TypeError("programming error"),
        RuntimeError("internal invariant failed"),
    ],
)
async def test_probe_propagates_non_provider_failures(error: BaseException) -> None:
    provider = _FakeEmbedProvider(error=error)
    service = EmbeddingService(provider, model="bge-m3")

    with pytest.raises(
        type(error),
        match=None if isinstance(error, asyncio.CancelledError) else str(error),
    ):
        await service.probe()

    assert service.last_unavailable_reason == ""


async def test_probe_bypasses_cache_and_hits_provider_each_call() -> None:
    # A cached success must never mask a provider that later goes down, so
    # probe() issues a real provider call instead of reading the cache.
    provider = _FakeEmbedProvider(vector=[0.5, 0.5])
    service = EmbeddingService(provider, model="bge-m3")

    await service.probe()
    await service.probe()

    assert len(provider.calls) == 2


def test_embedding_cache_get_rejects_non_list_payload(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, vector, model) VALUES (?, ?, ?)",
        ("bad-object", '{"oops": 1}', ""),
    )
    cache.conn.commit()

    assert cache.get("bad-object") is None


def test_embedding_cache_get_rejects_non_numeric_vectors(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, vector, model) VALUES (?, ?, ?)",
        ("bad-vector", '[1, "oops", 3]', ""),
    )
    cache.conn.commit()

    assert cache.get("bad-vector") is None


def test_embedding_cache_is_thread_safe_across_threads(tmp_path: Path) -> None:
    # Regression: discovery candidate post-processing and recommendation prewarm
    # touch the cache from worker threads other than the one that opened it. A
    # bare sqlite3 connection (check_same_thread=True) raises "SQLite objects
    # created in a thread can only be used in that same thread".
    import threading

    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()  # connection opened on this (main) thread

    errors: list[Exception] = []
    results: dict[str, object] = {}

    def worker() -> None:
        try:
            cache.put("k", [0.1, 0.2, 0.3], model="bge-m3")
            results["get"] = cache.get("k")
            results["count"] = cache.count()
        except Exception as exc:  # noqa: BLE001 — capture for assertion
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == [], f"cache raised across threads: {errors}"
    assert results["get"] == [0.1, 0.2, 0.3]
    assert results["count"] == 1


def test_gemini_multimodal_embedding_model_detection() -> None:
    assert GeminiProvider.is_multimodal_embedding_model("gemini-embedding-2")
    assert GeminiProvider.is_multimodal_embedding_model("gemini-embedding-2-preview")
    assert not GeminiProvider.is_multimodal_embedding_model("gemini-embedding-001")
    assert not GeminiProvider.is_multimodal_embedding_model("bge-m3")
    assert not GeminiProvider.is_multimodal_embedding_model("")


async def test_embed_image_inactive_when_multimodal_disabled() -> None:
    provider = _FakeImageEmbedProvider()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        multimodal_enabled=False,
    )

    assert service.supports_image_embedding is True
    assert service.image_embedding_active() is False
    assert await service.embed_image(b"fake-jpeg-bytes") == []
    assert provider.image_calls == []


async def test_embed_image_inactive_for_text_only_model() -> None:
    provider = _FakeImageEmbedProvider()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-001",
        multimodal_enabled=True,
    )

    assert service.supports_image_embedding is False
    assert await service.embed_image(b"fake-jpeg-bytes") == []
    assert provider.image_calls == []


@pytest.mark.parametrize(
    "error_type",
    [ValueError, TypeError, RuntimeError, AssertionError, asyncio.CancelledError],
)
def test_direct_service_propagates_capability_checker_errors_before_cache_or_masking(
    tmp_path: Path,
    error_type: type[BaseException],
) -> None:
    error = error_type("direct provider capability checker failed")

    class ExplodingCapabilityProvider(_FakeImageEmbedProvider):
        def is_multimodal_embedding_model(self, model: str) -> bool:
            raise error

    provider = ExplodingCapabilityProvider()
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()

    with pytest.raises(error_type) as captured:
        EmbeddingService(
            provider,
            model="gemini-embedding-2",
            persistent_cache=cache,
            multimodal_enabled=True,
        )

    assert captured.value is error
    assert provider.calls == []
    assert provider.image_calls == []
    assert cache.count() == 0


async def test_embed_image_caches_and_reuses_vector(tmp_path: Path) -> None:
    provider = _FakeImageEmbedProvider(image_vector=[0.2, 0.4, 0.6])
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        cache_model="gemini-embedding-2#dim=1024",
        persistent_cache=cache,
        multimodal_enabled=True,
    )
    image = b"\xff\xd8\xff" + b"cover-bytes-demo"

    first = await service.embed_image(image, mime_type="image/jpeg")
    second = await service.embed_image(image, mime_type="image/jpeg")

    assert first == [0.2, 0.4, 0.6]
    assert second == first
    assert len(provider.image_calls) == 1
    key = image_embedding_cache_key(image)
    assert service.lookup_cached_image(key) == first
    assert cache.get(key, model="gemini-embedding-2#dim=1024") == first


async def test_embed_image_skips_cache_on_empty_vector() -> None:
    provider = _FakeImageEmbedProvider(image_vector=[])
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        multimodal_enabled=True,
    )
    image = b"empty-result"

    assert await service.embed_image(image) == []
    assert await service.embed_image(image) == []
    assert len(provider.image_calls) == 2


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        ValueError("request schema is invalid"),
        TypeError("programming error"),
        RuntimeError("internal invariant failed"),
    ],
)
async def test_embed_image_propagates_non_provider_failures_without_cache_or_masking(
    tmp_path: Path,
    error: BaseException,
) -> None:
    provider = _FakeImageEmbedProvider(image_error=error)
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        persistent_cache=cache,
        multimodal_enabled=True,
    )

    with pytest.raises(
        type(error),
        match=None if isinstance(error, asyncio.CancelledError) else str(error),
    ):
        await service.embed_image(b"private image")

    assert cache.count() == 0
    assert service.last_unavailable_reason == ""


async def test_text_only_provider_has_no_image_support() -> None:
    provider = _FakeEmbedProvider()
    service = EmbeddingService(
        provider,
        model="bge-m3",
        multimodal_enabled=True,
    )
    assert service.supports_image_embedding is False
    assert service.image_embedding_active() is False


async def test_ordered_route_failure_degrades_safely_and_never_caches_invalid_vector(
    tmp_path: Path,
) -> None:
    sentinel = "Bearer provider-raw-secret"
    settings = EmbeddingModelSettings(model="bge-m3", output_dimensionality=2)

    class FailingAdapter:
        name = "safe-id"
        connection_type = "ollama"
        preset = ""
        supports_image_embedding = False

        def __init__(self) -> None:
            self.settings = settings
            self.calls = 0

        async def embed(self, text: str) -> list[float]:
            self.calls += 1
            raise LLMProviderError(sentinel)

    adapter = FailingAdapter()
    route = OrderedEmbeddingRoute((adapter,), settings=settings, revision="r1")
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    service = EmbeddingService(route, persistent_cache=cache)

    assert await service.embed("private text") == []
    assert await service.embed("private text") == []
    assert adapter.calls == 2
    assert cache.count() == 0
    assert sentinel not in service.last_unavailable_reason


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        ValueError("request schema is invalid"),
        TypeError("programming error"),
        RuntimeError("internal invariant failed"),
    ],
)
async def test_embed_propagates_non_provider_failures_without_cache_or_masking(
    tmp_path: Path,
    error: BaseException,
) -> None:
    provider = _FakeEmbedProvider(error=error)
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    service = EmbeddingService(provider, model="bge-m3", persistent_cache=cache)

    with pytest.raises(
        type(error),
        match=None if isinstance(error, asyncio.CancelledError) else str(error),
    ):
        await service.embed("private text")

    assert cache.count() == 0
    assert service.last_unavailable_reason == ""


async def test_ordered_route_settings_are_the_only_service_model_space_source() -> None:
    settings = EmbeddingModelSettings(
        model="shared-model",
        output_dimensionality=2,
        similarity_threshold=0.61,
        multimodal_enabled=False,
    )

    class Adapter:
        name = "endpoint"
        connection_type = "openai_compatible"
        preset = "custom"
        supports_image_embedding = False

        def __init__(self) -> None:
            self.settings = settings

        async def embed(self, text: str) -> list[float]:
            return [1.0, 0.0]

    route = OrderedEmbeddingRoute((Adapter(),), settings=settings, revision="r1")
    service = EmbeddingService(
        route,
        model="must-not-override-route",
        cache_model="must-not-contaminate-cache",
        similarity_threshold=0.99,
        multimodal_enabled=True,
    )

    assert await service.embed("text") == [1.0, 0.0]
    assert service._model == "shared-model"  # noqa: SLF001
    assert service._cache_model == settings.cache_namespace()  # noqa: SLF001
    assert service.similarity_threshold == 0.61
    assert service.multimodal_enabled is False
