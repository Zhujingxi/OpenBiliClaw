"""Durable model-specific embedding index with bounded brute-force recall."""

from __future__ import annotations

import hashlib
import math
import struct
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase

    from .protocol import EmbeddingModelInfo, EmbeddingProvider, Vector

EmbeddingKind: TypeAlias = Literal["evidence", "claim", "candidate"]
EmbeddingMatch: TypeAlias = tuple[str, float]
_ALLOWED_KINDS = frozenset({"evidence", "claim", "candidate"})
MAX_COSINE_SCAN_ENTRIES = 10_000


class EmbeddingIndex:
    """Persist float32 vectors and recall only entries for the configured model."""

    def __init__(
        self,
        database: SqliteDatabase,
        provider: EmbeddingProvider | None,
        model: EmbeddingModelInfo | None,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if (provider is None) != (model is None):
            raise ValueError("embedding provider and model must be configured together")
        self._database = database
        self._provider = provider
        self._model = model
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    async def upsert(self, kind: EmbeddingKind, ref_id: str, text: str) -> bool:
        """Embed changed text and persist one model-specific entry."""

        self._validate_kind(kind)
        if not ref_id or not text.strip():
            raise ValueError("embedding index reference and text must not be empty")
        if self._provider is None or self._model is None:
            return False
        model_id = self._model.identity
        entry_id = _entry_identity(kind, ref_id, model_id)
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        async with self._database.transaction() as session:
            existing = await session.fetch_one(
                "SELECT text_hash FROM embedding_index WHERE entry_id=?", (entry_id,)
            )
        if existing == (text_hash,):
            return False

        result = await self._provider.embed_documents((text,))
        if result.model.identity != model_id or len(result.vectors) != 1:
            raise ValueError("embedding provider returned mismatched model output")
        vector = result.vectors[0]
        self._validate_vector(vector)
        async with self._database.transaction() as session:
            await session.execute(
                "INSERT INTO embedding_index("
                "entry_id,kind,ref_id,model,vector,text_hash,created_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(entry_id) DO UPDATE SET "
                "vector=excluded.vector,text_hash=excluded.text_hash,created_at=excluded.created_at",
                (
                    entry_id,
                    kind,
                    ref_id,
                    model_id,
                    _pack(vector),
                    text_hash,
                    self._clock().isoformat(),
                ),
            )
        return True

    async def vector(self, kind: EmbeddingKind, ref_id: str) -> Vector | None:
        """Return a current-model stored vector for one opaque reference."""

        self._validate_kind(kind)
        if self._model is None:
            return None
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT vector FROM embedding_index WHERE kind=? AND ref_id=? AND model=?",
                (kind, ref_id, self._model.identity),
            )
        if row is None or not isinstance(row[0], bytes):
            return None
        vector = _unpack(row[0])
        return vector if len(vector) == self._model.dimensions else None

    async def query(
        self,
        *,
        text: str | None = None,
        vector: Vector | None = None,
        kinds: tuple[EmbeddingKind, ...],
        limit: int,
    ) -> tuple[EmbeddingMatch, ...]:
        """Return cosine-ranked opaque references for the current model."""

        if (text is None) == (vector is None):
            raise ValueError("provide exactly one embedding query input")
        if text is not None and not text.strip():
            raise ValueError("embedding query text must not be empty")
        if not kinds or any(kind not in _ALLOWED_KINDS for kind in kinds):
            raise ValueError("embedding query kinds are invalid")
        if not 1 <= limit <= 100:
            raise ValueError("embedding query limit must be between 1 and 100")
        if self._provider is None or self._model is None:
            return ()
        query_vector = await self._provider.embed_query(text) if text is not None else vector
        assert query_vector is not None
        self._validate_vector(query_vector)
        placeholders = ",".join("?" for _ in kinds)
        # ponytail: brute-force cosine is sufficient for the bounded ~10k local index;
        # replace this scan with sqlite-vec only when measured size/latency requires ANN.
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT ref_id,vector FROM embedding_index WHERE model=? AND kind IN ("
                + placeholders
                + ") ORDER BY created_at DESC,entry_id LIMIT ?",
                (self._model.identity, *kinds, MAX_COSINE_SCAN_ENTRIES),
            )
        matches = []
        for ref_id, payload in rows:
            if not isinstance(payload, bytes):
                continue
            stored = _unpack(payload)
            if len(stored) != self._model.dimensions:
                continue
            matches.append((str(ref_id), _cosine(query_vector, stored)))
        matches.sort(key=lambda item: (-item[1], item[0]))
        return tuple(matches[:limit])

    def _validate_vector(self, vector: Vector) -> None:
        assert self._model is not None
        if len(vector) != self._model.dimensions or any(not math.isfinite(item) for item in vector):
            raise ValueError("embedding vector does not match configured model")

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in _ALLOWED_KINDS:
            raise ValueError("embedding index kind is invalid")


def _entry_identity(kind: str, ref_id: str, model_id: str) -> str:
    return "emb_" + hashlib.sha256(f"{kind}:{ref_id}:{model_id}".encode()).hexdigest()[:32]


def _pack(vector: Vector) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(payload: bytes) -> Vector:
    if len(payload) % 4:
        return ()
    return tuple(struct.unpack(f"<{len(payload) // 4}f", payload))


def _cosine(left: Vector, right: Vector) -> float:
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    score = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    if not math.isfinite(score):  # out-of-band blob corruption must not rank first
        return 0.0
    return max(-1.0, min(1.0, score))
