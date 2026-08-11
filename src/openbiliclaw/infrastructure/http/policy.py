"""Explicit shared HTTP transport policy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy for safe/idempotent requests."""

    max_attempts: int = 3
    backoff_seconds: float = 0.1
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.backoff_seconds < 0:
            raise ValueError("invalid HTTP retry policy")


@dataclass(frozen=True, slots=True)
class HttpPolicy:
    """Scoped HTTP client defaults; ambient proxies are disabled by default."""

    timeout_seconds: float = 20.0
    verify_tls: bool = True
    trust_env: bool = False
    proxy: str | None = None
    user_agent: str = "OpenBiliClaw"
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive")
        if not self.user_agent.strip():
            raise ValueError("HTTP user-agent must not be empty")
