"""Bounded offload for synchronous application ports used by async transports."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from anyio import CapacityLimiter, to_thread

if TYPE_CHECKING:
    from collections.abc import Callable

_P = ParamSpec("_P")
_R = TypeVar("_R")

# API polling can overlap across clients, but a shared bound prevents disconnected
# clients from growing the worker-thread population without limit.
_SYNC_PORT_LIMITER = CapacityLimiter(16)


async def run_sync_port(func: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs) -> _R:
    """Run a sync port off-loop while waiting for side effects to finish on cancellation."""

    return await to_thread.run_sync(
        partial(func, *args, **kwargs),
        abandon_on_cancel=False,
        limiter=_SYNC_PORT_LIMITER,
    )


__all__ = ["run_sync_port"]
