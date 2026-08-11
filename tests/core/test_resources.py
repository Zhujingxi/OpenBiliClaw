from __future__ import annotations

import asyncio

import pytest

from openbiliclaw.core.resources import ResourceBudget


async def test_resource_budget_bounds_concurrency_without_sleeping() -> None:
    budget = ResourceBudget("model", limit=1)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_attempted = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with budget.acquire():
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        second_attempted.set()
        async with budget.acquire():
            second_entered.set()

    first_task = asyncio.create_task(first())
    await first_entered.wait()
    second_task = asyncio.create_task(second())
    await second_attempted.wait()
    assert not second_entered.is_set()
    assert budget.active == 1

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert second_entered.is_set()
    assert budget.active == 0


async def test_cancelled_waiter_does_not_consume_a_slot() -> None:
    budget = ResourceBudget("database", limit=1)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def holder() -> None:
        async with budget.acquire():
            entered.set()
            await release.wait()

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    waiting_slot = budget.acquire()
    waiter = asyncio.create_task(waiting_slot.__aenter__())
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await holder_task

    async with budget.acquire():
        assert budget.active == 1
    assert budget.name == "database"
    assert budget.limit == 1


def test_resource_budget_validates_identity_and_limit() -> None:
    with pytest.raises(ValueError):
        ResourceBudget("", limit=1)
    with pytest.raises(ValueError):
        ResourceBudget("model", limit=0)
