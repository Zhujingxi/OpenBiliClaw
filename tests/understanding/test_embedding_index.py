from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.ai.providers.embeddings import (
    EmbeddingModelInfo,
    EmbeddingResult,
    EmbeddingUsage,
)
from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.ai.providers.embeddings import Vector

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def model(name: str = "test-v1") -> EmbeddingModelInfo:
    return EmbeddingModelInfo(
        provider="test", model=name, dimensions=2, normalized=True, version="1"
    )


class FakeEmbeddings:
    def __init__(self, info: EmbeddingModelInfo, vectors: dict[str, Vector]) -> None:
        self.info = info
        self.vectors = vectors
        self.document_calls = 0

    async def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingResult:
        self.document_calls += 1
        return EmbeddingResult(
            vectors=tuple(self.vectors[text] for text in texts),
            usage=EmbeddingUsage(requests=1, input_tokens=len(texts)),
            model=self.info,
        )

    async def embed_query(self, text: str) -> Vector:
        return self.vectors[text]


async def open_index(
    path: Path,
    provider: FakeEmbeddings | None,
    info: EmbeddingModelInfo | None,
) -> tuple[SqliteDatabase, EmbeddingIndex]:
    assert await SchemaMigrator(path).migrate() == 11
    database = SqliteDatabase(path)
    await database.open()
    return database, EmbeddingIndex(database, provider, info, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_index_round_trip_orders_cosine_and_skips_unchanged_text(tmp_path: Path) -> None:
    info = model()
    provider = FakeEmbeddings(
        info,
        {
            "query": (1.0, 0.0),
            "alpha": (1.0, 0.0),
            "alpha changed": (0.0, 1.0),
            "near": (0.8, 0.2),
            "far": (0.0, 1.0),
        },
    )
    database, index = await open_index(tmp_path / "index.db", provider, info)
    try:
        assert await index.upsert("candidate", "candidate-a", "alpha")
        assert await index.upsert("candidate", "candidate-b", "near")
        assert await index.upsert("candidate", "candidate-c", "far")
        assert not await index.upsert("candidate", "candidate-a", "alpha")
        assert provider.document_calls == 3
        matches = await index.query(text="query", kinds=("candidate",), limit=3)
        assert tuple(item[0] for item in matches) == (
            "candidate-a",
            "candidate-b",
            "candidate-c",
        )
        assert tuple(item[1] for item in matches) == pytest.approx((1.0, 0.9701425, 0.0))

        assert await index.upsert("candidate", "candidate-a", "alpha changed")
        assert provider.document_calls == 4
        assert await index.vector("candidate", "candidate-a") == pytest.approx((0.0, 1.0))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_index_ignores_entries_from_a_different_model(tmp_path: Path) -> None:
    path = tmp_path / "models.db"
    old_info = model("old")
    old_provider = FakeEmbeddings(old_info, {"old document": (1.0, 0.0)})
    database, old_index = await open_index(path, old_provider, old_info)
    assert await old_index.upsert("claim", "claim-old", "old document")
    await database.close()

    new_info = model("new")
    new_provider = FakeEmbeddings(new_info, {"query": (1.0, 0.0)})
    database = SqliteDatabase(path)
    await database.open()
    try:
        new_index = EmbeddingIndex(database, new_provider, new_info, clock=lambda: NOW)
        assert await new_index.query(text="query", kinds=("claim",), limit=10) == ()
        assert await new_index.vector("claim", "claim-old") is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_unconfigured_index_is_a_noop(tmp_path: Path) -> None:
    database, index = await open_index(tmp_path / "disabled.db", None, None)
    try:
        assert not index.enabled
        assert not await index.upsert("evidence", "ev_" + "1" * 32, "summary")
        assert await index.query(text="anything", kinds=("evidence",), limit=10) == ()
        assert await index.vector("evidence", "ev_" + "1" * 32) is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_index_guards_reject_bad_input_and_corrupt_rows(tmp_path: Path) -> None:
    info = model()
    provider = FakeEmbeddings(info, {"query": (1.0, 0.0), "alpha": (1.0, 0.0), "zero": (0.0, 0.0)})
    database, index = await open_index(tmp_path / "guards.db", provider, info)
    try:
        with pytest.raises(ValueError, match="kind is invalid"):
            index._validate_kind("profile")  # noqa: SLF001 - guard boundary test
        with pytest.raises(ValueError, match="limit"):
            await index.query(text="query", kinds=("claim",), limit=0)
        with pytest.raises(ValueError, match="empty"):
            await index.upsert("claim", "ref", "  ")
        mismatched = FakeEmbeddings(info, {"odd": (1.0, 0.0, 0.5)})
        bad_index = EmbeddingIndex(database, mismatched, info, clock=lambda: NOW)
        # Provider returns a 3-dim vector for the 2-dim configured model: rejected.
        with pytest.raises(ValueError, match="does not match"):
            await bad_index.upsert("claim", "ref-3d", "odd")

        await index.upsert("claim", "zero-claim", "zero")
        await index.upsert("claim", "real-claim", "alpha")
        # Zero-norm stored vector scores 0.0, never crashes the scan.
        assert await index.query(text="query", kinds=("claim",), limit=5) == (
            ("real-claim", 1.0),
            ("zero-claim", 0.0),
        )
        # Corrupt payload (not a multiple of 4) is skipped, not fatal.
        async with database.transaction() as session:
            await session.execute(
                "UPDATE embedding_index SET vector = X'0102' WHERE ref_id = 'real-claim'"
            )
        assert await index.query(text="query", kinds=("claim",), limit=5) == (("zero-claim", 0.0),)
    finally:
        await database.close()
