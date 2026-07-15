"""Deterministic ordered Chat routing with shared revision-aware circuits."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from .base import (
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    classify_llm_failure_kind,
    retry_after_seconds_from_exception,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openbiliclaw.model_config import ChatConnection

RouteFailureKind: TypeAlias = Literal[
    "rate_limited",
    "auth_failed",
    "model_not_found",
    "timeout",
    "connection",
    "server_error",
    "invalid_response",
    "moderation",
]
CircuitFailureKind: TypeAlias = RouteFailureKind | Literal["config_error"]

_FALLBACK_KINDS = frozenset(
    {
        "rate_limited",
        "auth_failed",
        "model_not_found",
        "timeout",
        "connection",
        "server_error",
        "invalid_response",
        "moderation",
        "config_error",
    }
)
_TRANSIENT_CIRCUIT_KINDS = frozenset({"timeout", "connection", "server_error"})
_PERMANENT_CIRCUIT_KINDS = frozenset({"auth_failed", "model_not_found", "config_error"})
_PROMPT_SCOPED_KINDS = frozenset({"invalid_response", "moderation"})
# Approved design calibration (2026-07-15): start at 15 seconds so ten dead
# endpoints cannot stall every background call, double to a five-minute cap,
# and recalibrate from production failure logs after any provider/model change.
_TRANSIENT_COOLDOWNS = (15.0, 30.0, 60.0, 120.0, 240.0, 300.0)
# Provider Retry-After takes precedence; 60 seconds is the design fallback for
# rate-limit responses that omit a usable header.
_RATE_LIMIT_COOLDOWN = 60.0

_SAFE_SUMMARIES: dict[str, str] = {
    "rate_limited": "The connection is rate limited or out of quota.",
    "auth_failed": "The connection authentication failed.",
    "model_not_found": "The configured model was not found.",
    "timeout": "The connection timed out.",
    "connection": "The connection could not reach its endpoint.",
    "server_error": "The connection returned a server error.",
    "invalid_response": "The connection returned an invalid response.",
    "moderation": "The connection declined this request under its content policy.",
    "config_error": "The connection is incompatible with the configured model space.",
}


@dataclass(frozen=True)
class RouteConnection:
    """One immutable config record bound to its private runtime adapter."""

    connection: ChatConnection
    adapter: LLMProvider = field(repr=False, compare=False)

    @property
    def id(self) -> str:
        return self.connection.id

    @property
    def type(self) -> str:
        return self.connection.type

    @property
    def preset(self) -> str:
        return self.connection.preset

    @property
    def model(self) -> str:
        return self.connection.model


@dataclass(frozen=True)
class RouteAttempt:
    """Secret-safe summary of one failed route attempt."""

    connection_id: str
    connection_type: str
    preset: str
    route_position: int
    failure_kind: RouteFailureKind
    summary: str

    @classmethod
    def safe(
        cls,
        connection: RouteConnection,
        position: int,
        failure_kind: RouteFailureKind,
    ) -> RouteAttempt:
        """Build a fixed-text attempt without retaining an upstream exception."""
        return cls(
            connection_id=connection.id,
            connection_type=connection.type,
            preset=connection.preset,
            route_position=position,
            failure_kind=failure_kind,
            summary=_SAFE_SUMMARIES[failure_kind],
        )


class LLMRouteExhaustedError(LLMFallbackError):
    """Raised with safe structured attempts when no route peer succeeds."""

    def __init__(self, attempts: Sequence[RouteAttempt]) -> None:
        self.attempts = tuple(attempts)
        if self.attempts:
            rendered = ", ".join(
                f"{attempt.connection_id}:{attempt.failure_kind}" for attempt in self.attempts
            )
            message = f"All configured LLM connections failed ({rendered})."
        else:
            message = "No provider was available to process the request."
        super().__init__(message)


@dataclass(frozen=True)
class CircuitState:
    """Secret-safe runtime circuit state for one connection and revision."""

    revision: str
    failure_kind: CircuitFailureKind
    opened_at: float
    retry_at: float | None
    failure_count: int

    @property
    def permanent(self) -> bool:
        return self.retry_at is None


class CircuitTable:
    """In-memory circuit states keyed by stable route ID and revision."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._states: dict[tuple[str, str], CircuitState] = {}

    def state_for(self, connection_id: str, revision: str) -> CircuitState | None:
        """Return exactly one revision's state without touching any peer."""
        return self._states.get((connection_id, revision))

    def should_skip(self, connection_id: str, revision: str) -> bool:
        """Return whether the current revision's circuit is still open."""
        state = self.state_for(connection_id, revision)
        if state is None or state.permanent:
            return state is not None
        retry_at = state.retry_at
        return retry_at is not None and retry_at > self._clock()

    def record_failure(
        self,
        connection_id: str,
        revision: str,
        failure_kind: str,
        exc: BaseException,
    ) -> None:
        """Open the category-specific circuit without retaining ``exc``."""
        if failure_kind not in _FALLBACK_KINDS:
            return
        kind = cast("CircuitFailureKind", failure_kind)
        if kind in _PROMPT_SCOPED_KINDS:
            return

        now = self._clock()
        key = (connection_id, revision)
        previous = self.state_for(connection_id, revision)
        if kind in _PERMANENT_CIRCUIT_KINDS:
            candidate = CircuitState(
                revision=revision,
                failure_kind=kind,
                opened_at=now,
                retry_at=None,
                failure_count=0,
            )
        elif kind == "rate_limited":
            delay = retry_after_seconds_from_exception(exc) or _RATE_LIMIT_COOLDOWN
            candidate = CircuitState(
                revision=revision,
                failure_kind=kind,
                opened_at=now,
                retry_at=now + delay,
                failure_count=0,
            )
        elif kind in _TRANSIENT_CIRCUIT_KINDS:
            previous_count = previous.failure_count if previous is not None else 0
            count = previous_count + 1
            delay = _TRANSIENT_COOLDOWNS[min(count - 1, len(_TRANSIENT_COOLDOWNS) - 1)]
            candidate = CircuitState(
                revision=revision,
                failure_kind=kind,
                opened_at=now,
                retry_at=now + delay,
                failure_count=count,
            )
        else:
            return

        self._store_strongest_state(key, previous, candidate, now)

    def _store_strongest_state(
        self,
        key: tuple[str, str],
        previous: CircuitState | None,
        candidate: CircuitState,
        now: float,
    ) -> None:
        """Merge a failed exact probe without weakening an open circuit."""
        if previous is not None:
            # Permanent auth/model/config protection is released only by
            # success for this exact revision. A failed exact probe cannot
            # replace it.
            if previous.permanent:
                return
            # Keep the entire prior state when it is still open and carries an
            # equal or later deadline, so kind/count describe the protection
            # that actually remains in force. Expired states advance normally.
            if (
                previous.retry_at is not None
                and previous.retry_at > now
                and candidate.retry_at is not None
                and candidate.retry_at <= previous.retry_at
            ):
                return
        self._states[key] = candidate

    def record_success(self, connection_id: str, revision: str) -> None:
        """Close only the successful connection revision's circuit."""
        self._states.pop((connection_id, revision), None)


class OrderedLLMRoute:
    """Execute one global ordered Chat route under a total deadline."""

    def __init__(
        self,
        connections: Sequence[RouteConnection],
        revision: str,
        timeout_seconds: float,
        clock: Callable[[], float] | None = None,
        *,
        circuits: CircuitTable | None = None,
    ) -> None:
        self.connections = tuple(connections)
        self.revision = revision
        self.timeout_seconds = float(timeout_seconds)
        self._clock = clock or time.monotonic
        self.circuits = circuits or CircuitTable(clock=self._clock)

    @property
    def default_provider(self) -> str:
        """Compatibility name for callers that inspect the primary ID."""
        return self.connections[0].id if self.connections else ""

    def get(self, connection_id: str) -> LLMProvider:
        """Return the exact adapter by stable ID."""
        connection, _position = self._find_connection(connection_id)
        return connection.adapter

    def is_chat_capable(self, connection_id: str) -> bool:
        """Return whether the stable ID belongs to this Chat route."""
        return any(connection.id == connection_id for connection in self.connections)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Try eligible peers in exact array order within one total deadline."""
        deadline = self._clock() + self.timeout_seconds
        attempts: list[RouteAttempt] = []
        for position, connection in enumerate(self.connections):
            if self.circuits.should_skip(connection.id, self.revision):
                continue
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            response = await self._attempt(
                connection,
                position,
                messages,
                remaining=remaining,
                attempts=attempts,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
                model=model,
            )
            if response is not None:
                return response
        raise LLMRouteExhaustedError(attempts)

    async def complete_connection(
        self,
        connection_id: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
        ignore_circuit: bool = False,
    ) -> LLMResponse:
        """Call one stable ID only; exact probes may bypass its open circuit."""
        connection, position = self._find_connection(connection_id)
        attempts: list[RouteAttempt] = []
        state = self.circuits.state_for(connection.id, self.revision)
        if not ignore_circuit and self.circuits.should_skip(connection.id, self.revision):
            if state is not None:
                # Chat adapters never record the embedding-only config_error kind.
                attempts.append(
                    RouteAttempt.safe(
                        connection,
                        position,
                        cast("RouteFailureKind", state.failure_kind),
                    )
                )
            raise LLMRouteExhaustedError(attempts)
        response = await self._attempt(
            connection,
            position,
            messages,
            remaining=self.timeout_seconds,
            attempts=attempts,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            reasoning_effort=reasoning_effort,
            model=model,
        )
        if response is None:
            raise LLMRouteExhaustedError(attempts)
        return response

    async def _attempt(
        self,
        connection: RouteConnection,
        position: int,
        messages: list[dict[str, Any]],
        *,
        remaining: float,
        attempts: list[RouteAttempt],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: str | None,
        model: str | None,
    ) -> LLMResponse | None:
        try:
            async with asyncio.timeout(remaining):
                response = await connection.adapter.complete(
                    cast("list[dict[str, str]]", messages),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    reasoning_effort=reasoning_effort,
                    model=model,
                )
        except Exception as exc:
            failure_kind = classify_llm_failure_kind(exc)
            if not self._should_fallback(exc, failure_kind):
                raise
            kind = cast("RouteFailureKind", failure_kind)
            attempts.append(RouteAttempt.safe(connection, position, kind))
            self.circuits.record_failure(connection.id, self.revision, kind, exc)
            return None
        self.circuits.record_success(connection.id, self.revision)
        return replace(
            response,
            connection_id=connection.id,
            connection_type=connection.type,
            preset=connection.preset,
            route_position=position,
        )

    @staticmethod
    def _should_fallback(exc: BaseException, failure_kind: str | None) -> bool:
        """Accept only classified provider/transport failures as route-local."""
        return bool(
            failure_kind in _FALLBACK_KINDS
            and isinstance(exc, (LLMProviderError, TimeoutError, ConnectionError, OSError))
        )

    def _find_connection(self, connection_id: str) -> tuple[RouteConnection, int]:
        for position, connection in enumerate(self.connections):
            if connection.id == connection_id:
                return connection, position
        raise KeyError(f"unknown LLM connection: {connection_id}")
