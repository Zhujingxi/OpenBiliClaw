"""Ordered Chat route, deadline, and circuit-breaker contract tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from openbiliclaw.llm.base import (
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)
from openbiliclaw.llm.route import (
    CircuitTable,
    LLMRouteExhaustedError,
    OrderedLLMRoute,
    RouteConnection,
)
from openbiliclaw.model_config import ChatConnection


@dataclass
class FakeClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeAdapter:
    connection_id: str
    outcomes: list[LLMResponse | BaseException]
    call_log: list[str]
    clock: FakeClock | None = None
    elapsed_per_call: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.connection_id

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.call_log.append(self.connection_id)
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_mode": json_mode,
                "reasoning_effort": reasoning_effort,
                "model": model,
            }
        )
        if self.clock is not None:
            self.clock.advance(self.elapsed_per_call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _connection(connection_id: str, adapter: FakeAdapter) -> RouteConnection:
    return RouteConnection(
        connection=ChatConnection(
            id=connection_id,
            name=f"Connection {connection_id}",
            type="openai_compatible",
            preset="openai",
            model=f"configured-{connection_id}",
        ),
        adapter=adapter,
    )


def _route(
    ids: tuple[str, ...],
    outcomes: tuple[LLMResponse | BaseException, ...],
    *,
    clock: FakeClock | None = None,
    timeout_seconds: float = 300.0,
) -> tuple[OrderedLLMRoute, list[FakeAdapter], list[str]]:
    call_log: list[str] = []
    adapters = [
        FakeAdapter(connection_id, [outcome], call_log)
        for connection_id, outcome in zip(ids, outcomes, strict=True)
    ]
    return (
        OrderedLLMRoute(
            tuple(
                _connection(connection_id, adapter)
                for connection_id, adapter in zip(ids, adapters, strict=True)
            ),
            revision="revision-a",
            timeout_seconds=timeout_seconds,
            clock=clock,
        ),
        adapters,
        call_log,
    )


@pytest.mark.asyncio
async def test_route_treats_same_type_connections_as_distinct_ordered_peers() -> None:
    route, _adapters, call_log = _route(
        ("openai-primary", "openai-second", "openai-third"),
        (
            LLMRateLimitError("rate limit"),
            LLMTimeoutError("request timed out"),
            LLMResponse(content="ok", provider="upstream", model="served-model"),
        ),
    )

    response = await route.complete([{"role": "user", "content": "hi"}])

    assert call_log == ["openai-primary", "openai-second", "openai-third"]
    assert response.connection_id == "openai-third"
    assert response.connection_type == "openai_compatible"
    assert response.preset == "openai"
    assert response.route_position == 2
    assert response.provider == "upstream"
    assert response.model == "served-model"


@pytest.mark.asyncio
async def test_route_passes_every_call_option_to_the_selected_adapter() -> None:
    route, adapters, _call_log = _route(
        ("only",),
        (LLMResponse(content="ok", provider="upstream", model="served"),),
    )
    messages = [{"role": "user", "content": "hi"}]

    await route.complete(
        messages,
        temperature=0.25,
        max_tokens=321,
        json_mode=True,
        reasoning_effort="high",
        model="per-call-model",
    )

    assert adapters[0].calls == [
        {
            "messages": messages,
            "temperature": 0.25,
            "max_tokens": 321,
            "json_mode": True,
            "reasoning_effort": "high",
            "model": "per-call-model",
        }
    ]


@pytest.mark.asyncio
async def test_provider_transport_retries_finish_before_next_connection() -> None:
    events: list[str] = []

    class RetryingAdapter(FakeAdapter):
        async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
            events.extend(["primary.transport.1", "primary.transport.2", "primary.transport.3"])
            raise LLMTimeoutError("transport retries exhausted")

    primary = RetryingAdapter("primary", [], events)
    fallback = FakeAdapter(
        "fallback",
        [LLMResponse(content="ok", provider="upstream")],
        events,
    )
    route = OrderedLLMRoute(
        (_connection("primary", primary), _connection("fallback", fallback)),
        revision="revision-a",
        timeout_seconds=30,
    )

    await route.complete([{"role": "user", "content": "hi"}])

    assert events == [
        "primary.transport.1",
        "primary.transport.2",
        "primary.transport.3",
        "fallback",
    ]


@pytest.mark.asyncio
async def test_each_attempt_receives_only_the_remaining_route_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    call_log: list[str] = []
    primary = FakeAdapter(
        "primary",
        [LLMTimeoutError("timeout")],
        call_log,
        clock=clock,
        elapsed_per_call=4,
    )
    fallback = FakeAdapter(
        "fallback",
        [LLMResponse(content="ok", provider="upstream")],
        call_log,
        clock=clock,
        elapsed_per_call=1,
    )
    route = OrderedLLMRoute(
        (_connection("primary", primary), _connection("fallback", fallback)),
        revision="revision-a",
        timeout_seconds=10,
        clock=clock,
    )
    observed_timeouts: list[float] = []

    @asynccontextmanager
    async def capture_timeout(seconds: float) -> Any:
        observed_timeouts.append(seconds)
        yield

    monkeypatch.setattr("openbiliclaw.llm.route.asyncio.timeout", capture_timeout)

    await route.complete([{"role": "user", "content": "hi"}])

    assert observed_timeouts == [10.0, 6.0]
    assert call_log == ["primary", "fallback"]


@pytest.mark.asyncio
async def test_route_does_not_start_a_fallback_after_deadline_exhaustion() -> None:
    clock = FakeClock()
    call_log: list[str] = []
    primary = FakeAdapter(
        "primary",
        [LLMTimeoutError("timeout")],
        call_log,
        clock=clock,
        elapsed_per_call=10,
    )
    fallback = FakeAdapter(
        "fallback",
        [LLMResponse(content="must not run")],
        call_log,
    )
    route = OrderedLLMRoute(
        (_connection("primary", primary), _connection("fallback", fallback)),
        revision="revision-a",
        timeout_seconds=10,
        clock=clock,
    )

    with pytest.raises(LLMRouteExhaustedError) as exc_info:
        await route.complete([{"role": "user", "content": "hi"}])

    assert call_log == ["primary"]
    assert [attempt.connection_id for attempt in exc_info.value.attempts] == ["primary"]


@pytest.mark.asyncio
async def test_asyncio_deadline_cancels_current_attempt_without_starting_fallback() -> None:
    call_log: list[str] = []
    never = asyncio.Event()

    class BlockingAdapter(FakeAdapter):
        async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
            call_log.append(self.connection_id)
            await never.wait()
            raise AssertionError("unreachable")

    primary = BlockingAdapter("primary", [], call_log)
    fallback = FakeAdapter("fallback", [LLMResponse(content="must not run")], call_log)
    route = OrderedLLMRoute(
        (_connection("primary", primary), _connection("fallback", fallback)),
        revision="revision-a",
        timeout_seconds=0.01,
    )

    with pytest.raises(LLMRouteExhaustedError) as exc_info:
        await route.complete([{"role": "user", "content": "hi"}])

    assert call_log == ["primary"]
    assert exc_info.value.attempts[0].failure_kind == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        ValueError("request schema is invalid"),
        TypeError("programming error"),
        RuntimeError("internal invariant failed"),
    ],
)
async def test_non_provider_failures_propagate_without_fallback(error: BaseException) -> None:
    route, _adapters, call_log = _route(
        ("primary", "fallback"),
        (error, LLMResponse(content="must not run")),
    )

    with pytest.raises(
        type(error), match=None if isinstance(error, asyncio.CancelledError) else str(error)
    ):
        await route.complete([{"role": "user", "content": "hi"}])

    assert call_log == ["primary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (LLMRateLimitError("rate limit"), "rate_limited"),
        (LLMProviderError("authentication failed: HTTP 401"), "auth_failed"),
        (LLMProviderError("configured model not found"), "model_not_found"),
        (LLMTimeoutError("request timed out"), "timeout"),
        (ConnectionError("connection refused"), "connection"),
        (LLMProviderError("server error: HTTP 503"), "server_error"),
        (LLMResponseError("invalid response"), "invalid_response"),
        (LLMProviderError("content policy refusal"), "moderation"),
    ],
)
async def test_provider_scoped_failure_kinds_fallback_for_the_current_call(
    error: BaseException,
    kind: str,
) -> None:
    route, _adapters, call_log = _route(
        ("primary", "fallback"),
        (error, LLMResponse(content="ok", provider="upstream")),
    )

    response = await route.complete([{"role": "user", "content": "hi"}])

    assert call_log == ["primary", "fallback"]
    assert response.connection_id == "fallback"
    state = route.circuits.state_for("primary", "revision-a")
    if kind in {"invalid_response", "moderation"}:
        assert state is None
    else:
        assert state is not None
        assert state.failure_kind == kind


@pytest.mark.asyncio
async def test_aggregate_attempts_and_error_text_never_retain_upstream_secrets() -> None:
    sentinel = "credential=top-secret https://user:password@gateway.test/private-body"
    route, _adapters, _call_log = _route(
        ("safe-id",),
        (LLMProviderError(f"server error: HTTP 503 {sentinel}"),),
    )

    with pytest.raises(LLMRouteExhaustedError) as exc_info:
        await route.complete([{"role": "user", "content": "hi"}])

    error = exc_info.value
    rendered = f"{error!s}\n{error!r}\n{error.attempts!r}"
    assert sentinel not in rendered
    assert "top-secret" not in rendered
    assert "user:password" not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.attempts[0].connection_id == "safe-id"
    assert error.attempts[0].failure_kind == "server_error"
    assert error.attempts[0].summary == "The connection returned a server error."


@pytest.mark.parametrize("kind", ["rate_limited"])
def test_rate_limit_circuit_uses_retry_after_then_default_cooldown(kind: str) -> None:
    clock = FakeClock()
    table = CircuitTable(clock=clock)
    table.record_failure(
        "chat-a",
        "revision-a",
        kind,
        LLMRateLimitError("rate limit", retry_after_seconds=17),
    )
    assert table.should_skip("chat-a", "revision-a")
    clock.advance(16.99)
    assert table.should_skip("chat-a", "revision-a")
    clock.advance(0.01)
    assert not table.should_skip("chat-a", "revision-a")

    table.record_success("chat-a", "revision-a")
    table.record_failure(
        "chat-a",
        "revision-a",
        kind,
        LLMRateLimitError("rate limit"),
    )
    clock.advance(59.99)
    assert table.should_skip("chat-a", "revision-a")
    clock.advance(0.01)
    assert not table.should_skip("chat-a", "revision-a")


@pytest.mark.parametrize("kind", ["auth_failed", "model_not_found"])
def test_permanent_circuit_stays_open_until_revision_change_or_success(kind: str) -> None:
    clock = FakeClock()
    table = CircuitTable(clock=clock)

    table.record_failure("chat-a", "revision-a", kind, LLMProviderError(kind))
    clock.advance(86_400)

    assert table.should_skip("chat-a", "revision-a")
    assert not table.should_skip("chat-a", "revision-b")
    state = table.state_for("chat-a", "revision-a")
    assert state is not None
    assert state.failure_kind == kind


@pytest.mark.asyncio
async def test_shared_circuit_table_isolates_interleaved_route_revisions() -> None:
    clock = FakeClock()
    circuits = CircuitTable(clock=clock)
    old_calls: list[str] = []
    new_calls: list[str] = []
    old_route = OrderedLLMRoute(
        (
            _connection(
                "chat-a",
                FakeAdapter(
                    "old-chat-a",
                    [
                        LLMProviderError("authentication failed: HTTP 401"),
                        LLMResponse(content="old-probe-ok"),
                        LLMProviderError("authentication failed: HTTP 401"),
                    ],
                    old_calls,
                ),
            ),
        ),
        revision="revision-old",
        timeout_seconds=30,
        clock=clock,
        circuits=circuits,
    )
    new_route = OrderedLLMRoute(
        (
            _connection(
                "chat-a",
                FakeAdapter(
                    "new-chat-a",
                    [LLMTimeoutError("request timed out"), LLMResponse(content="new-probe-ok")],
                    new_calls,
                ),
            ),
        ),
        revision="revision-new",
        timeout_seconds=30,
        clock=clock,
        circuits=circuits,
    )

    with pytest.raises(LLMRouteExhaustedError):
        await old_route.complete([{"role": "user", "content": "old failure"}])
    with pytest.raises(LLMRouteExhaustedError):
        await new_route.complete([{"role": "user", "content": "new failure"}])

    old_state = circuits.state_for("chat-a", "revision-old")
    new_state = circuits.state_for("chat-a", "revision-new")
    assert old_state is not None and old_state.failure_kind == "auth_failed"
    assert new_state is not None and new_state.failure_kind == "timeout"

    old_probe = await old_route.complete_connection(
        "chat-a",
        [{"role": "user", "content": "old probe"}],
        ignore_circuit=True,
    )
    assert old_probe.content == "old-probe-ok"
    assert circuits.state_for("chat-a", "revision-old") is None
    assert circuits.state_for("chat-a", "revision-new") == new_state

    with pytest.raises(LLMRouteExhaustedError):
        await old_route.complete([{"role": "user", "content": "old failure again"}])
    reopened_old_state = circuits.state_for("chat-a", "revision-old")
    assert reopened_old_state is not None

    new_probe = await new_route.complete_connection(
        "chat-a",
        [{"role": "user", "content": "new probe"}],
        ignore_circuit=True,
    )
    assert new_probe.content == "new-probe-ok"
    assert circuits.state_for("chat-a", "revision-new") is None
    assert circuits.state_for("chat-a", "revision-old") == reopened_old_state
    assert old_calls == ["old-chat-a", "old-chat-a", "old-chat-a"]
    assert new_calls == ["new-chat-a", "new-chat-a"]


@pytest.mark.parametrize("kind", ["timeout", "connection", "server_error"])
def test_transient_circuit_uses_bounded_exponential_ladder(kind: str) -> None:
    clock = FakeClock()
    table = CircuitTable(clock=clock)

    for delay in (15, 30, 60, 120, 240, 300, 300):
        table.record_failure("chat-a", "revision-a", kind, LLMProviderError(kind))
        clock.advance(delay - 0.01)
        assert table.should_skip("chat-a", "revision-a")
        clock.advance(0.01)
        assert not table.should_skip("chat-a", "revision-a")


@pytest.mark.parametrize("kind", ["invalid_response", "moderation"])
def test_prompt_scoped_failures_do_not_open_cross_request_circuit(kind: str) -> None:
    table = CircuitTable(clock=FakeClock())

    table.record_failure("chat-a", "revision-a", kind, LLMProviderError(kind))

    assert not table.should_skip("chat-a", "revision-a")
    assert table.state_for("chat-a", "revision-a") is None


@pytest.mark.parametrize(
    ("probe_kind", "probe_error"),
    [
        ("moderation", LLMProviderError("content policy refusal")),
        ("timeout", LLMTimeoutError("request timed out")),
        ("rate_limited", LLMRateLimitError("rate limit", retry_after_seconds=5)),
    ],
)
def test_failed_exact_probe_cannot_replace_a_permanent_circuit(
    probe_kind: str,
    probe_error: LLMProviderError,
) -> None:
    table = CircuitTable(clock=FakeClock())
    table.record_failure(
        "chat-a",
        "revision-a",
        "auth_failed",
        LLMProviderError("authentication failed"),
    )

    table.record_failure(
        "chat-a",
        "revision-a",
        probe_kind,
        probe_error,
    )

    assert table.should_skip("chat-a", "revision-a")
    state = table.state_for("chat-a", "revision-a")
    assert state is not None
    assert state.failure_kind == "auth_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_error", "probe_error", "expected_kind", "expected_count"),
    [
        (
            LLMRateLimitError("rate limit", retry_after_seconds=120),
            LLMTimeoutError("request timed out"),
            "rate_limited",
            0,
        ),
        (
            LLMTimeoutError("request timed out"),
            LLMRateLimitError("rate limit", retry_after_seconds=5),
            "timeout",
            1,
        ),
    ],
)
async def test_failed_exact_probe_never_shortens_an_open_timed_circuit(
    first_error: LLMProviderError,
    probe_error: LLMProviderError,
    expected_kind: str,
    expected_count: int,
) -> None:
    clock = FakeClock()
    route, adapters, _call_log = _route(
        ("chat-a",),
        (first_error,),
        clock=clock,
    )
    adapters[0].outcomes.append(probe_error)

    with pytest.raises(LLMRouteExhaustedError):
        await route.complete_connection(
            "chat-a",
            [{"role": "user", "content": "open"}],
            ignore_circuit=True,
        )
    original = route.circuits.state_for("chat-a", route.revision)
    assert original is not None and original.retry_at is not None

    clock.advance(1)
    with pytest.raises(LLMRouteExhaustedError):
        await route.complete_connection(
            "chat-a",
            [{"role": "user", "content": "failed probe"}],
            ignore_circuit=True,
        )

    retained = route.circuits.state_for("chat-a", route.revision)
    assert retained == original
    assert retained.failure_kind == expected_kind
    assert retained.failure_count == expected_count


@pytest.mark.asyncio
async def test_failed_exact_probe_extends_timed_circuit_for_a_later_deadline() -> None:
    clock = FakeClock()
    route, adapters, _call_log = _route(
        ("chat-a",),
        (LLMTimeoutError("request timed out"),),
        clock=clock,
    )
    adapters[0].outcomes.append(LLMRateLimitError("rate limit", retry_after_seconds=30))

    with pytest.raises(LLMRouteExhaustedError):
        await route.complete_connection(
            "chat-a",
            [{"role": "user", "content": "open"}],
            ignore_circuit=True,
        )
    original = route.circuits.state_for("chat-a", route.revision)
    assert original is not None and original.retry_at is not None

    clock.advance(1)
    with pytest.raises(LLMRouteExhaustedError):
        await route.complete_connection(
            "chat-a",
            [{"role": "user", "content": "later probe"}],
            ignore_circuit=True,
        )

    extended = route.circuits.state_for("chat-a", route.revision)
    assert extended is not None and extended.retry_at is not None
    assert extended.retry_at > original.retry_at
    assert extended.failure_kind == "rate_limited"
    assert extended.failure_count == 0


@pytest.mark.asyncio
async def test_exact_probe_bypasses_open_circuit_and_success_closes_it() -> None:
    call_log: list[str] = []
    adapter = FakeAdapter(
        "chat-a",
        [
            LLMProviderError("authentication failed: HTTP 401"),
            LLMResponse(content="probe-ok", provider="upstream"),
            LLMResponse(content="route-ok", provider="upstream"),
        ],
        call_log,
    )
    route = OrderedLLMRoute(
        (_connection("chat-a", adapter),),
        revision="revision-a",
        timeout_seconds=30,
    )

    with pytest.raises(LLMRouteExhaustedError):
        await route.complete([{"role": "user", "content": "first"}])
    with pytest.raises(LLMRouteExhaustedError):
        await route.complete([{"role": "user", "content": "skipped"}])

    probe = await route.complete_connection(
        "chat-a",
        [{"role": "user", "content": "probe"}],
        ignore_circuit=True,
    )
    response = await route.complete([{"role": "user", "content": "after"}])

    assert probe.content == "probe-ok"
    assert probe.connection_id == "chat-a"
    assert response.content == "route-ok"
    assert call_log == ["chat-a", "chat-a", "chat-a"]
    assert route.circuits.state_for("chat-a", "revision-a") is None


@pytest.mark.asyncio
async def test_complete_connection_calls_only_the_requested_stable_id() -> None:
    route, adapters, call_log = _route(
        ("first", "second", "third"),
        (
            LLMResponse(content="first"),
            LLMResponse(content="second"),
            LLMResponse(content="third", provider="upstream"),
        ),
    )

    response = await route.complete_connection(
        "third",
        [{"role": "user", "content": "probe"}],
        model="probe-model",
    )

    assert response.content == "third"
    assert response.connection_id == "third"
    assert response.route_position == 2
    assert call_log == ["third"]
    assert adapters[2].calls[0]["model"] == "probe-model"


@pytest.mark.asyncio
async def test_complete_connection_rejects_unknown_id_without_calling_any_adapter() -> None:
    route, _adapters, call_log = _route(
        ("only",),
        (LLMResponse(content="must not run"),),
    )

    with pytest.raises(KeyError, match="unknown LLM connection"):
        await route.complete_connection(
            "missing",
            [{"role": "user", "content": "probe"}],
        )

    assert call_log == []
