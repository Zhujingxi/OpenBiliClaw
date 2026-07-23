"""Wave 0 contracts for worker-only dialogue settlement mutation.

Spec: docs/plans/2026-07-23-dialogue-settlement-queue-spec.md §5.2.
Plan: docs/plans/2026-07-23-dialogue-settlement-queue-plan.md Task 0.2.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from openbiliclaw.api.app import create_app


@dataclass(frozen=True, slots=True)
class _ProtectedMutatorCategory:
    name: str
    production_symbol: str
    protected_symbols: tuple[str, ...]


PROTECTED_MUTATORS = (
    _ProtectedMutatorCategory(
        "hypothesis_dialogue_apply",
        "SoulEngine.settle_hypothesis",
        ("SoulEngine._apply_dialogue_settlement[hypothesis]",),
    ),
    _ProtectedMutatorCategory(
        "confusion_dialogue_apply",
        "SoulEngine.settle_confusion_answer",
        ("SoulEngine._apply_dialogue_settlement[confusion]",),
    ),
    _ProtectedMutatorCategory(
        "speculation_dialogue_apply",
        "SoulEngine.settle_speculation",
        ("SoulEngine._apply_dialogue_settlement[speculation]",),
    ),
    _ProtectedMutatorCategory(
        "anchor_mutations",
        "DialogueAnchorManager.establish",
        (
            "DialogueAnchorManager.establish",
            "DialogueAnchorManager.release",
            "DialogueAnchorManager.note_relation",
            "DialogueAnchorManager.expire",
        ),
    ),
    _ProtectedMutatorCategory(
        "confusion_lifecycle",
        "ConfusionManager.schedule_ask",
        (
            "ConfusionManager.schedule_ask",
            "ConfusionManager.process_anchor_settlement",
            "ConfusionManager.retry_anchor_settlements",
        ),
    ),
    _ProtectedMutatorCategory(
        "card_payload_transition",
        "create_app._defer_hypothesis_card",
        ("card.payload.transition",),
    ),
    _ProtectedMutatorCategory(
        "card_projection_reconcile",
        "create_app._reconcile_chat_card_row",
        ("card.projection", "card.reconcile"),
    ),
    _ProtectedMutatorCategory(
        "dialogue_confirmation_cooldown",
        "create_app._defer_dialogue_confirmation",
        ("dialogue_confirmation.cooldown",),
    ),
    _ProtectedMutatorCategory(
        "probe_durable_reply_side_effect",
        "create_app._apply_durable_chat_success_side_effects",
        ("probe.reply.side_effect",),
    ),
    _ProtectedMutatorCategory(
        "confusion_durable_reply_side_effect",
        "create_app._apply_durable_chat_success_side_effects",
        ("confusion.reply.side_effect",),
    ),
)


@dataclass(frozen=True, slots=True)
class _RawSink:
    name: str
    source_symbol: str
    operations: tuple[str, ...]
    current_callsites: tuple[tuple[int, str], ...]


RAW_SINK_INVENTORY = (
    _RawSink(
        "confusion_schedule",
        "ConfusionManager.schedule_ask",
        ("schedule",),
        ((2806, "confusion_manager.schedule_ask("),),
    ),
    _RawSink(
        "confusion_ask_turn_update",
        "Database.update_confusion",
        ("retarget", "create_failure_rollback"),
        (
            (2822, "updater("),
            (2926, 'updater(int(ref), status="open", ask_turn_id="")'),
            (2931, "updater("),
        ),
    ),
    _RawSink(
        "pending_open_anchor",
        "DialogueAnchorManager.establish",
        ("establish",),
        ((2945, "anchor_manager.establish("),),
    ),
)


def _production_symbol_source(symbol: str) -> str:
    owner_name, function_name = symbol.split(".", 1)
    if owner_name == "SoulEngine":
        from openbiliclaw.soul.engine import SoulEngine

        return inspect.getsource(getattr(SoulEngine, function_name))
    if owner_name == "DialogueAnchorManager":
        from openbiliclaw.soul.dialogue_anchor import DialogueAnchorManager

        return inspect.getsource(getattr(DialogueAnchorManager, function_name))
    if owner_name == "ConfusionManager":
        from openbiliclaw.soul.confusion import ConfusionManager

        return inspect.getsource(getattr(ConfusionManager, function_name))
    assert owner_name == "create_app"
    source = inspect.getsource(create_app)
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == function_name
    ]
    assert len(matches) == 1
    node = matches[0]
    assert node.end_lineno is not None
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def test_protected_mutator_inventory_has_ten_dialogue_categories() -> None:
    """Q1/F4: freeze the ten representative protected mutation categories."""
    assert len(PROTECTED_MUTATORS) == 10
    assert len({category.name for category in PROTECTED_MUTATORS}) == 10
    assert all(category.protected_symbols for category in PROTECTED_MUTATORS)


def test_pending_open_raw_sink_inventory_is_complete() -> None:
    """Q1/F3: freeze schedule, retarget/rollback, and establish raw sinks."""
    assert [sink.source_symbol for sink in RAW_SINK_INVENTORY] == [
        "ConfusionManager.schedule_ask",
        "Database.update_confusion",
        "DialogueAnchorManager.establish",
    ]
    assert {operation for sink in RAW_SINK_INVENTORY for operation in sink.operations} == {
        "schedule",
        "retarget",
        "create_failure_rollback",
        "establish",
    }
    app_lines = (
        (Path(__file__).parents[1] / "src/openbiliclaw/api/app.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    for sink in RAW_SINK_INVENTORY:
        for line_number, snippet in sink.current_callsites:
            assert snippet in app_lines[line_number - 1]


@pytest.mark.parametrize("category", PROTECTED_MUTATORS, ids=lambda item: item.name)
async def test_protected_mutator_cannot_mutate_outside_worker(
    category: _ProtectedMutatorCategory,
) -> None:
    """Q1/F4: calling a protected mutator outside the worker changes nothing."""
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementGuard,
        DialogueSettlementMutationOutsideWorker,
    )

    guard = DialogueSettlementGuard()
    mutations: list[str] = []

    def protected_mutator() -> None:
        guard.require_dialogue_settlement_worker()
        mutations.append(category.name)

    with pytest.raises(DialogueSettlementMutationOutsideWorker):
        protected_mutator()
    assert mutations == []


async def test_worker_child_task_cannot_inherit_mutation_permit() -> None:
    """Q14/F4: ContextVar inheritance never authorizes a child task."""
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementGuard,
        DialogueSettlementMutationOutsideWorker,
    )

    guard = DialogueSettlementGuard()
    worker_task = asyncio.current_task()
    assert worker_task is not None
    mutations: list[str] = []

    def protected_mutator(label: str) -> None:
        guard.require_dialogue_settlement_worker()
        mutations.append(label)

    async def child_mutation() -> None:
        protected_mutator("child")

    with guard.dialogue_settlement_worker(worker_task):
        protected_mutator("worker")
        child = asyncio.create_task(child_mutation())
        with pytest.raises(DialogueSettlementMutationOutsideWorker):
            await child

    with pytest.raises(DialogueSettlementMutationOutsideWorker):
        protected_mutator("after-worker")
    assert mutations == ["worker"]


async def test_module_level_guard_context_requires_actual_worker_identity() -> None:
    """F4: the finalized module-level context and require functions share one guard."""
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementMutationOutsideWorker,
        dialogue_settlement_worker,
        require_dialogue_settlement_worker,
    )

    worker_task = asyncio.current_task()
    assert worker_task is not None
    with pytest.raises(DialogueSettlementMutationOutsideWorker):
        require_dialogue_settlement_worker()
    with dialogue_settlement_worker(worker_task):
        require_dialogue_settlement_worker()
    with pytest.raises(DialogueSettlementMutationOutsideWorker):
        require_dialogue_settlement_worker()


async def test_old_worker_finally_cannot_clear_new_worker_permit_after_reload_handoff() -> None:
    """Q14/R2-3: old cleanup compare-and-clear cannot revoke the new worker."""
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementGuard,
        DialogueSettlementMutationOutsideWorker,
    )

    guard = DialogueSettlementGuard()
    loop = asyncio.get_running_loop()
    old_permit_future = loop.create_future()
    new_permit_future = loop.create_future()
    old_active = asyncio.Event()
    old_revoked = asyncio.Event()
    old_at_finally = asyncio.Event()
    allow_old_cleanup = asyncio.Event()
    old_cleaned = asyncio.Event()
    new_first_success = asyncio.Event()
    old_denials: list[str] = []
    new_mutations: list[str] = []
    old_clear_results: list[bool] = []
    new_clear_results: list[bool] = []

    def protected_mutator(target: str) -> None:
        guard.require_dialogue_settlement_worker()
        if target == "new":
            new_mutations.append(target)

    async def old_worker() -> None:
        permit = await old_permit_future
        try:
            with guard.activate_worker(permit):
                old_active.set()
                await old_revoked.wait()
                with pytest.raises(DialogueSettlementMutationOutsideWorker):
                    protected_mutator("old")
                old_denials.append("after-revoke")
        finally:
            old_at_finally.set()
            await allow_old_cleanup.wait()
            old_clear_results.append(guard.clear_if_current(permit))
            with pytest.raises(DialogueSettlementMutationOutsideWorker):
                protected_mutator("old")
            old_denials.append("after-finally")

    async def new_worker() -> None:
        permit = await new_permit_future
        try:
            with guard.activate_worker(permit):
                protected_mutator("new")
                new_first_success.set()
                await old_cleaned.wait()
                protected_mutator("new")
        finally:
            new_clear_results.append(guard.clear_if_current(permit))

    old_task = asyncio.create_task(old_worker(), name="wave0-old-dialogue-worker")
    old_permit = guard.register_worker(old_task)
    old_permit_future.set_result(old_permit)
    await asyncio.wait_for(old_active.wait(), timeout=1)

    assert guard.revoke_worker(old_permit) is True
    old_revoked.set()
    await asyncio.wait_for(old_at_finally.wait(), timeout=1)

    new_task = asyncio.create_task(new_worker(), name="wave0-new-dialogue-worker")
    new_permit = guard.register_worker(new_task)
    new_permit_future.set_result(new_permit)
    await asyncio.wait_for(new_first_success.wait(), timeout=1)

    allow_old_cleanup.set()
    await asyncio.wait_for(old_task, timeout=1)
    old_cleaned.set()
    await asyncio.wait_for(new_task, timeout=1)

    assert old_denials == ["after-revoke", "after-finally"]
    assert new_mutations == ["new", "new"]
    assert old_clear_results == [False]
    assert new_clear_results == [True]


@pytest.mark.parametrize("category", PROTECTED_MUTATORS, ids=lambda item: item.name)
@pytest.mark.xfail(
    strict=True,
    reason="[Q1/F4] protected production façade wiring is installed during Wave 3 cutover",
)
def test_protected_production_wiring_requires_worker_guard(
    category: _ProtectedMutatorCategory,
) -> None:
    """Q1/F4: freeze the production guard installation points without cutting over."""
    source = _production_symbol_source(category.production_symbol)
    assert "require_dialogue_settlement_worker" in source
