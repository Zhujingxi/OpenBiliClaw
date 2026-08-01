"""Dialogue settlement queue invariants frozen in Wave 0 and implemented by Wave 1.

Registry, owner, commit-point, permit, and typed queue cases are GREEN in Wave
1. Wave 2 removes the settlement fence; endpoint cutover remains in Wave 3.

Spec: docs/plans/2026-07-23-dialogue-settlement-queue-spec.md
Plan: docs/plans/2026-07-23-dialogue-settlement-queue-plan.md Task 0.1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from openbiliclaw.soul.dialogue_learn_queue import (
    ANCHOR_ESTABLISH_PRODUCER_SOURCES,
    AnchorAbsent,
    AnchorAdmissionError,
    AnchorMutationDisposition,
    AnchorMutationTerminal,
    AnchorPersisted,
    AnchorReserved,
    DialogueDispatchResult,
    DialogueJob,
    DialogueJobKind,
    DialogueJobResult,
    DialogueSettlementQueue,
    DialogueSettlementReentryError,
    anchor_snapshot_as_mapping,
    anchor_transition_as_mapping,
)

_DispatchHook = Callable[[dict[str, object], dict[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _DialogueEntry:
    spec_label: str
    source_symbol: str
    job_kinds: tuple[str, ...]
    protected_mutations: tuple[str, ...]
    waits_for_completion: bool


ENTRY_INVENTORY = (
    _DialogueEntry(
        "卡片 `confirm/reject`",
        "api.app.create_app.act_on_chat_card",
        ("settle.hypothesis",),
        ("hypothesis.apply", "card.project", "anchor.release"),
        True,
    ),
    _DialogueEntry(
        "卡片 `defer`",
        "api.app.create_app.act_on_chat_card",
        ("card.defer",),
        ("card.transition", "confirmation.cooldown", "anchor.release"),
        True,
    ),
    _DialogueEntry(
        "卡片 `discuss`",
        "api.app.create_app.act_on_chat_card",
        ("card.discuss",),
        ("card.transition", "anchor.establish"),
        True,
    ),
    _DialogueEntry(
        "pending open / confusion question claim、retarget、rollback",
        "api.app.create_app._create_confirmation_turn",
        ("confusion.open.sync", "anchor.establish"),
        ("confusion.schedule", "confusion.retarget", "confusion.rollback"),
        True,
    ),
    _DialogueEntry(
        "durable confusion turn 建锚",
        "api.app.create_app._ensure_confusion_dialogue_anchor",
        ("anchor.establish",),
        ("anchor.establish",),
        True,
    ),
    _DialogueEntry(
        "锚 relation/解除/归属结算",
        "soul.engine.SoulEngine._process_dialogue_anchor_decision",
        ("learn",),
        ("anchor.relation", "dialogue_object.apply", "anchor.release"),
        True,
    ),
    _DialogueEntry(
        "普通 chat `settles`",
        "soul.engine.SoulEngine._process_dialogue_settles",
        ("learn",),
        ("dialogue_object.apply",),
        False,
    ),
    _DialogueEntry(
        "durable `scope=probe` 回复侧效应",
        "api.app.create_app._apply_durable_chat_success_side_effects",
        ("probe.reply.apply",),
        ("probe.reply.side_effect",),
        True,
    ),
    _DialogueEntry(
        "durable `scope=confusion` 回复侧效应",
        "api.app.create_app._apply_durable_chat_success_side_effects",
        ("confusion.reply.apply",),
        ("confusion.reply.side_effect",),
        True,
    ),
    _DialogueEntry(
        "confusion attribution 补放",
        "soul.engine.SoulEngine.replay_confusion_dialogue_attributions",
        ("confusion.attribution.replay",),
        ("confusion.apply", "anchor.establish"),
        False,
    ),
    _DialogueEntry(
        "legacy insight feedback",
        "api.app.create_app.insight_feedback",
        ("settle.hypothesis",),
        ("hypothesis.apply", "card.project", "anchor.release"),
        True,
    ),
    _DialogueEntry(
        "card projection / orphan discussion repair",
        "api.app.create_app._reconcile_chat_card_row",
        ("card.reconcile",),
        ("card.project", "card.reconcile"),
        False,
    ),
)

OUT_OF_SCOPE_WRITERS = (
    "force_tick",
    "avoidance",
    "exploration",
    "profile_pipeline",
    "openclaw",
    "cli",
)


def test_dialogue_entry_inventory_matches_spec_section_2_2() -> None:
    """Q1/F3: every finalized §2.2 table row is classified exactly once."""
    spec = (
        Path(__file__).parents[1] / "docs/plans/2026-07-23-dialogue-settlement-queue-spec.md"
    ).read_text(encoding="utf-8")
    section = spec.split("### 2.2 必须入队的入口", 1)[1].split("### 2.3 Out of scope", 1)[0]
    spec_labels = []
    for line in section.splitlines():
        if not line.startswith("| "):
            continue
        label = line.split("|", 2)[1].strip()
        if label not in {"入口", "---"}:
            spec_labels.append(label)

    inventory_labels = [entry.spec_label for entry in ENTRY_INVENTORY]
    unclassified = sorted(set(spec_labels) - set(inventory_labels))
    duplicate_labels = sorted(
        label for label in set(inventory_labels) if inventory_labels.count(label) != 1
    )
    assert inventory_labels == spec_labels
    assert unclassified == []
    assert duplicate_labels == []
    assert all(entry.job_kinds for entry in ENTRY_INVENTORY)
    assert all(entry.protected_mutations for entry in ENTRY_INVENTORY)


def test_dialogue_entry_inventory_excludes_out_of_scope_writers() -> None:
    """Q1 boundary: Wave 0 cannot pull the explicitly excluded writers inward."""
    classified = {
        token
        for entry in ENTRY_INVENTORY
        for token in (*entry.job_kinds, *entry.protected_mutations)
    }
    assert classified.isdisjoint(OUT_OF_SCOPE_WRITERS)


async def _capture_typed_admissions(
    jobs: list[dict[str, object]],
    *,
    dispatch: _DispatchHook,
) -> dict[str, dict[str, object]]:
    """Queue jobs behind a barrier and return immutable typed admission copies."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    observed: dict[str, dict[str, object]] = {}
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()

    async def handler(job: DialogueJob) -> DialogueJobResult | AnchorMutationTerminal:
        record = dict(job.payload)
        job_id = str(record.get("job_id", ""))
        record["anchor_snapshot"] = anchor_snapshot_as_mapping(job.anchor_snapshot)
        record["anchor_transition"] = anchor_transition_as_mapping(job.anchor_transition)
        record["owned_anchor_reservation_id"] = job.owned_anchor_reservation_id or ""
        record["owner_job_id"] = job.job_id
        record["owner_sequence"] = job.sequence
        observed[job_id] = record
        if job_id == "wave0-blocker":
            blocker_entered.set()
            await release_blocker.wait()
            return DialogueJobResult(outcome="completed")
        await dispatch(record, persisted)
        if job.owned_anchor_reservation_id is not None:
            target_kind = str(record["target_kind"])
            target_ref = str(record["target_ref"])
            if (
                persisted.get("anchor_ref") == target_ref
                and int(persisted.get("anchor_generation", 0)) > 0
            ):
                return AnchorMutationTerminal.persisted(
                    kind=target_kind,
                    ref=target_ref,
                    generation=int(persisted["anchor_generation"]),
                )
            return AnchorMutationTerminal.absent(
                target_kind=target_kind,
                target_ref=target_ref,
            )
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        assert queue.submit(DialogueJobKind.LEARN, {"job_id": "wave0-blocker"})
        await asyncio.wait_for(blocker_entered.wait(), timeout=1)
        for job in jobs:
            kind = DialogueJobKind(str(job["kind"]))
            assert queue.submit(kind, job)
        release_blocker.set()
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_blocker.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)
    return observed


async def _no_dispatch(
    _payload: dict[str, object],
    _persisted: dict[str, object],
) -> None:
    return


async def test_worker_await_does_not_block_event_loop_heartbeat() -> None:
    """Q5: an awaiting worker yields without any synchronous settlement fence."""
    worker_entered = asyncio.Event()
    release_worker = asyncio.Event()
    heartbeat_ticks = 0

    async def handler(_job: DialogueJob) -> DialogueJobResult:
        worker_entered.set()
        await release_worker.wait()
        return DialogueJobResult(outcome="completed")

    async def heartbeat() -> None:
        nonlocal heartbeat_ticks
        for _ in range(20):
            await asyncio.sleep(0)
            heartbeat_ticks += 1

    queue = DialogueSettlementQueue(handler)
    job = queue.submit(DialogueJobKind.LEARN, {"job_id": "llm-wait"}, completion=True)
    assert job is not None and job.completion is not None
    try:
        await asyncio.wait_for(worker_entered.wait(), timeout=1)
        await asyncio.wait_for(heartbeat(), timeout=1)
        assert heartbeat_ticks == 20
    finally:
        release_worker.set()
        await queue.shutdown(timeout=1)
    assert job.completion.result().outcome == "completed"


async def test_queued_anchor_reservation_is_visible_to_later_settlement_admission() -> None:
    """Q3/F2: an accepted establish job must be visible before it persists."""

    async def establish(
        payload: dict[str, object],
        persisted: dict[str, object],
    ) -> None:
        if payload.get("kind") == "anchor.establish":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=7)

    observed = await _capture_typed_admissions(
        [
            {
                "job_id": "builder",
                "kind": "anchor.establish",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "durable_confusion_ensure",
            },
            {
                "job_id": "settle",
                "kind": "settle.hypothesis",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
        ],
        dispatch=establish,
    )

    snapshot = observed["settle"].get("anchor_snapshot")
    assert isinstance(snapshot, Mapping), "F2 requires a typed admission snapshot"
    assert snapshot.get("state") == "reserved"
    assert snapshot.get("producer_kind") == "anchor.establish"
    assert snapshot.get("ref") == "hypothesis-A"
    assert snapshot.get("reservation_id")


async def test_no_anchor_tombstone_is_not_upgraded_by_later_establish_admission() -> None:
    """Q3/F2: a settle accepted without an anchor keeps an absent tombstone."""

    async def establish(
        payload: dict[str, object],
        persisted: dict[str, object],
    ) -> None:
        if payload.get("kind") == "anchor.establish":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=8)

    observed = await _capture_typed_admissions(
        [
            {
                "job_id": "settle",
                "kind": "settle.hypothesis",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            {
                "job_id": "builder",
                "kind": "anchor.establish",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "pending_probe_throw",
            },
        ],
        dispatch=establish,
    )

    snapshot = observed["settle"].get("anchor_snapshot")
    assert isinstance(snapshot, Mapping), "F2 forbids encoding absent as ('', 0)"
    assert snapshot.get("state") == "absent"
    assert snapshot.get("target_kind") == "hypothesis"
    assert snapshot.get("target_ref") == "hypothesis-A"
    assert int(snapshot.get("tombstone_epoch", 0)) > 0


async def test_card_discuss_reservation_is_visible_to_later_settlement_admission() -> None:
    """Q3/R2-1: card.discuss must reserve before its inline anchor mutation."""

    async def discuss(
        payload: dict[str, object],
        persisted: dict[str, object],
    ) -> None:
        if payload.get("kind") == "card.discuss":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=9)

    observed = await _capture_typed_admissions(
        [
            {
                "job_id": "discuss",
                "kind": "card.discuss",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            {
                "job_id": "settle",
                "kind": "settle.hypothesis",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
        ],
        dispatch=discuss,
    )

    snapshot = observed["settle"].get("anchor_snapshot")
    assert isinstance(snapshot, Mapping), "R2-1 requires discuss reservation visibility"
    assert snapshot.get("state") == "reserved"
    assert snapshot.get("producer_kind") == "card.discuss"
    assert snapshot.get("kind") == "hypothesis"
    assert snapshot.get("ref") == "hypothesis-A"


@pytest.mark.parametrize(
    ("job_kind", "producer_source", "needs_anchor"),
    [
        pytest.param("anchor.establish", "pending_probe_throw", True, id="probe-throw"),
        pytest.param(
            "anchor.establish",
            "pending_confusion_throw",
            True,
            id="confusion-throw",
        ),
        pytest.param(
            "anchor.establish",
            "durable_confusion_ensure",
            True,
            id="durable-confusion",
        ),
        pytest.param("card.discuss", "card_action", True, id="card-discuss"),
        pytest.param(
            "confusion.attribution.replay",
            "cognition_cycle",
            True,
            id="attribution-replay",
        ),
    ],
)
async def test_every_anchor_building_kind_reserves_before_enqueue(
    job_kind: str,
    producer_source: str,
    needs_anchor: bool,
) -> None:
    """Q3/R2-1: the builder policy is exhaustive at admission."""
    observed = await _capture_typed_admissions(
        [
            {
                "job_id": "builder",
                "kind": job_kind,
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": producer_source,
                "needs_anchor": needs_anchor,
            }
        ],
        dispatch=_no_dispatch,
    )

    builder = observed["builder"]
    transition = builder.get("anchor_transition")
    assert isinstance(transition, Mapping), "R2-1 requires exhaustive admission policy"
    assert transition.get("action") == "establish"
    assert transition.get("origin") == producer_source
    assert builder.get("owned_anchor_reservation_id")
    snapshot = builder.get("anchor_snapshot")
    assert isinstance(snapshot, Mapping)
    assert snapshot.get("state") == "reserved"


@pytest.mark.parametrize(
    "producer_source",
    ["card_action", "cognition_cycle", "undeclared_nonempty_source"],
)
async def test_anchor_establish_rejects_undeclared_producer_source(
    producer_source: str,
) -> None:
    """F6: anchor.establish admission is closed to undeclared producers."""
    assert producer_source not in ANCHOR_ESTABLISH_PRODUCER_SOURCES

    async def dispatcher(_job: DialogueJob) -> DialogueJobResult:
        raise AssertionError("Rejected admission must never dispatch")

    queue = DialogueSettlementQueue(dispatcher)

    with pytest.raises(AnchorAdmissionError, match="producer_source must be one of"):
        queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": producer_source,
            },
        )


@pytest.mark.parametrize(
    ("actual_ref", "actual_generation", "expected_state"),
    [
        pytest.param("hypothesis-A", 12, "persisted", id="actual-persisted"),
        pytest.param("", 0, "absent", id="actual-absent"),
    ],
)
async def test_failed_reservation_advances_head_for_new_submit_and_gc_after_old_dependents_drain(
    actual_ref: str,
    actual_generation: int,
    expected_state: str,
) -> None:
    """Q18/R2-2: failed is old-dependency-only and retry gets a fresh owner."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    failed_resolved = asyncio.Event()
    release_failed_builder = asyncio.Event()
    old_effects: list[str] = []

    async def handler(
        job: DialogueJob,
    ) -> DialogueJobResult | DialogueDispatchResult | AnchorMutationTerminal:
        label = str(job.payload["job_id"])
        if label == "builder-failed":
            persisted.update(anchor_ref=actual_ref, anchor_generation=actual_generation)
            actual = (
                AnchorPersisted("hypothesis", actual_ref, actual_generation)
                if actual_ref
                else AnchorAbsent("hypothesis", "hypothesis-A", 1)
            )

            async def after_resolution() -> None:
                failed_resolved.set()
                await release_failed_builder.wait()

            return DialogueDispatchResult(
                result=DialogueJobResult(outcome="failed"),
                anchor_terminal=AnchorMutationTerminal.failed(
                    actual,
                    cause="controlled builder failure",
                ),
                followup=after_resolution,
            )
        if label.startswith("old-"):
            old_effects.append(label)
            return DialogueJobResult(outcome="applied")
        if label == "retry-builder":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=13)
            return AnchorMutationTerminal.persisted(
                kind="hypothesis",
                ref="hypothesis-A",
                generation=13,
            )
        return DialogueJobResult(outcome="applied")

    queue = DialogueSettlementQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        builder = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "job_id": "builder-failed",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        assert builder is not None and builder.owned_anchor_reservation_id is not None
        failed_reservation_id = builder.owned_anchor_reservation_id
        old_jobs: list[DialogueJob] = []
        for index in (1, 2):
            old_job = queue.submit(
                DialogueJobKind.SETTLE_HYPOTHESIS,
                {
                    "job_id": f"old-{index}",
                    "target_kind": "hypothesis",
                    "target_ref": "hypothesis-A",
                },
                completion=True,
            )
            assert old_job is not None
            old_jobs.append(old_job)
        await asyncio.wait_for(failed_resolved.wait(), timeout=1)
        new_job = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "job_id": "new-after-failure",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            completion=True,
        )
        assert new_job is not None
        assert new_job.anchor_snapshot.state == expected_state
        assert queue.registry.reservation_reference_count(failed_reservation_id) == 3

        release_failed_builder.set()
        old_results = await asyncio.gather(
            *(asyncio.shield(job.completion) for job in old_jobs if job.completion is not None)
        )
        assert [result.outcome for result in old_results] == [
            "anchor_dependency_failed",
            "anchor_dependency_failed",
        ]
        assert old_effects == []
        assert not queue.registry.has_reservation(failed_reservation_id)
        retry = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "job_id": "retry-builder",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        assert retry is not None and retry.owned_anchor_reservation_id is not None
        assert retry.owned_anchor_reservation_id != failed_reservation_id
        assert retry.completion is not None
        retry_result = await asyncio.shield(retry.completion)
        assert retry_result.outcome == "persisted"
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_failed_builder.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)


async def test_failed_reservation_after_persisted_builder_keeps_b1_as_effective_head() -> None:
    """Q18/Q19/R2-2/M1: B1 persisted then B2 failed must expose B1 to new submit."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    b2_resolved = asyncio.Event()
    release_b2_followup = asyncio.Event()
    effects: list[str] = []

    async def handler(
        job: DialogueJob,
    ) -> DialogueJobResult | DialogueDispatchResult | AnchorMutationTerminal:
        label = str(job.payload["job_id"])
        if label == "B1":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=21)
            return AnchorMutationTerminal.persisted(
                kind="hypothesis",
                ref="hypothesis-A",
                generation=21,
            )
        if label == "B2":

            async def after_resolution() -> None:
                b2_resolved.set()
                await release_b2_followup.wait()

            return DialogueDispatchResult(
                result=DialogueJobResult(outcome="failed"),
                anchor_terminal=AnchorMutationTerminal.failed(
                    AnchorPersisted("hypothesis", "hypothesis-A", 21),
                    cause="B2 controlled failure",
                ),
                followup=after_resolution,
            )
        effects.append(label)
        return DialogueJobResult(outcome="applied")

    queue = DialogueSettlementQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        b1 = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "job_id": "B1",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        b2 = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "job_id": "B2",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        s1 = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "job_id": "S1",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            completion=True,
        )
        assert b1 is not None and b2 is not None and s1 is not None
        assert isinstance(s1.anchor_snapshot, AnchorReserved)
        assert s1.anchor_snapshot.reservation_id == b2.owned_anchor_reservation_id

        await asyncio.wait_for(b2_resolved.wait(), timeout=1)
        s2 = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "job_id": "S2",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            completion=True,
        )
        assert s2 is not None
        assert isinstance(s2.anchor_snapshot, AnchorPersisted)
        assert s2.anchor_snapshot.generation == 21
        assert s2.anchor_snapshot.resolved_by_reservation_id == b2.owned_anchor_reservation_id
        release_b2_followup.set()
        assert s1.completion is not None and s2.completion is not None
        s1_result, s2_result = await asyncio.gather(
            asyncio.shield(s1.completion),
            asyncio.shield(s2.completion),
        )
        assert s1_result.outcome == "anchor_dependency_failed"
        assert s2_result.outcome == "applied"
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_b2_followup.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)
    assert b1.owned_anchor_reservation_id
    assert b2.owned_anchor_reservation_id
    assert b1.owned_anchor_reservation_id != b2.owned_anchor_reservation_id
    assert effects == ["S2"]


@pytest.mark.parametrize(
    "builder_kind",
    [
        pytest.param("anchor.establish", id="anchor-establish"),
        pytest.param("card.discuss", id="card-discuss"),
        pytest.param("confusion.attribution.replay", id="attribution-replay"),
    ],
)
async def test_same_ref_double_builder_second_noop_resolves_own_head_for_later_settlement(
    builder_kind: str,
) -> None:
    """Q19/R3-1: same-ref builders never coalesce or resolve each other."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    outcomes: dict[str, str] = {}
    s1_finished = asyncio.Event()

    async def handler(
        job: DialogueJob,
    ) -> DialogueJobResult | AnchorMutationTerminal:
        label = str(job.payload["job_id"])
        if label == "B1":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=31)
            outcomes[label] = "persisted"
            return AnchorMutationTerminal.persisted(
                kind="hypothesis",
                ref="hypothesis-A",
                generation=31,
            )
        if label == "B2":
            outcomes[label] = "no_op"
            return AnchorMutationTerminal.no_op(AnchorPersisted("hypothesis", "hypothesis-A", 31))
        outcomes[label] = "applied"
        if label == "S1":
            s1_finished.set()
        return DialogueJobResult(outcome="applied")

    queue = DialogueSettlementQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        admitted_builders: list[DialogueJob] = []
        for job_id in ("B1", "B2"):
            payload: dict[str, object] = {
                "job_id": job_id,
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "needs_anchor": True,
            }
            if builder_kind == DialogueJobKind.ANCHOR_ESTABLISH.value:
                payload["producer_source"] = "durable_confusion_ensure"
            elif builder_kind == DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY.value:
                payload["producer_source"] = "cognition_cycle"
            builder = queue.submit(
                DialogueJobKind(builder_kind),
                payload,
                completion=True,
            )
            assert builder is not None
            admitted_builders.append(builder)
        s1 = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "job_id": "S1",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            completion=True,
        )
        assert s1 is not None
        b1, b2 = admitted_builders
        assert isinstance(b1.anchor_snapshot, AnchorReserved)
        assert isinstance(b2.anchor_snapshot, AnchorReserved)
        assert isinstance(s1.anchor_snapshot, AnchorReserved)
        assert b1.anchor_snapshot.reservation_id != b2.anchor_snapshot.reservation_id
        assert b1.anchor_snapshot.owner_job_id == b1.job_id
        assert b1.anchor_snapshot.owner_sequence == b1.sequence
        assert b2.anchor_snapshot.owner_job_id == b2.job_id
        assert b2.anchor_snapshot.owner_sequence == b2.sequence
        assert s1.anchor_snapshot.reservation_id == b2.anchor_snapshot.reservation_id

        await asyncio.wait_for(s1_finished.wait(), timeout=1)
        s2 = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "job_id": "S2",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            completion=True,
        )
        assert s2 is not None
        assert isinstance(s2.anchor_snapshot, AnchorPersisted)
        assert s2.anchor_snapshot.generation == 31
        assert s2.anchor_snapshot.resolved_by_reservation_id == b2.owned_anchor_reservation_id
        completions = [
            job.completion for job in (*admitted_builders, s1, s2) if job.completion is not None
        ]
        await asyncio.gather(*(asyncio.shield(completion) for completion in completions))
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        if queue.worker_alive:
            await queue.shutdown(timeout=1)

    assert outcomes == {
        "B1": "persisted",
        "B2": "no_op",
        "S1": "applied",
        "S2": "applied",
    }


@pytest.mark.parametrize(
    "terminal",
    [
        pytest.param("persisted", id="persisted-followup-throw"),
        pytest.param("absent", id="absent"),
        pytest.param("already_terminal", id="already-terminal-short-circuit"),
        pytest.param("no_op", id="duplicate-replay-no-op"),
        pytest.param("superseded", id="superseded"),
        pytest.param("failed", id="failed"),
    ],
)
async def test_anchor_reservation_promotes_before_followup_await_throw_or_replay_short_circuit(
    terminal: str,
) -> None:
    """Q20/R3-2: mutator return synchronously resolves its exact owner."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    followup_entered = asyncio.Event()
    release_followup = asyncio.Event()

    async def handler(
        job: DialogueJob,
    ) -> DialogueJobResult | DialogueDispatchResult:
        if job.payload.get("job_id") != "builder":
            return DialogueJobResult(outcome="applied")
        if terminal in {"absent", "failed"}:
            persisted.update(anchor_ref="", anchor_generation=0)
            actual: AnchorPersisted | AnchorAbsent = AnchorAbsent(
                "hypothesis",
                "hypothesis-A",
                1,
            )
        else:
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=41)
            actual = AnchorPersisted("hypothesis", "hypothesis-A", 41)

        disposition = AnchorMutationDisposition(terminal)
        typed_terminal = AnchorMutationTerminal(
            disposition=disposition,
            actual_state=actual,
            cause="controlled failure" if disposition is AnchorMutationDisposition.FAILED else "",
        )

        async def after_resolution() -> None:
            followup_entered.set()
            await release_followup.wait()
            if terminal == "persisted":
                raise RuntimeError("follow-up effect failed after durable anchor mutation")

        return DialogueDispatchResult(
            result=DialogueJobResult(outcome=terminal),
            anchor_terminal=typed_terminal,
            followup=after_resolution,
        )

    queue = DialogueSettlementQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        builder = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "job_id": "builder",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        assert builder is not None
        reservation_id = builder.owned_anchor_reservation_id
        assert reservation_id is not None
        await asyncio.wait_for(followup_entered.wait(), timeout=1)
        assert queue.registry.reservation_resolution_count(reservation_id) == 1
        assert queue.registry.reservation_terminal(reservation_id).disposition.value == terminal
        later = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "job_id": "later-settle",
                "target_kind": "hypothesis",
                "target_ref": "hypothesis-A",
            },
            completion=True,
        )
        assert later is not None
        assert later.anchor_snapshot.state in {"persisted", "absent"}
        release_followup.set()
        assert builder.completion is not None and later.completion is not None
        if terminal == "persisted":
            with pytest.raises(RuntimeError, match="follow-up effect failed"):
                await asyncio.shield(builder.completion)
        else:
            builder_result = await asyncio.shield(builder.completion)
            assert builder_result.outcome == terminal
        later_result = await asyncio.shield(later.completion)
        assert later_result.outcome == "applied"
        head = queue.registry.head(
            target_kind="hypothesis",
            target_ref="hypothesis-A",
        )
        assert head.state in {"persisted", "absent"}
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_followup.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)


def test_dialogue_job_kind_whitelist_is_exhaustive() -> None:
    """Q1/F1/F3: Wave 1 exposes every finalized dispatcher kind exactly once."""
    from openbiliclaw.soul import dialogue_learn_queue

    assert {kind.value for kind in dialogue_learn_queue.DialogueJobKind} == {
        "learn",
        "settle.hypothesis",
        "settle.confusion",
        "card.defer",
        "card.discuss",
        "card.reconcile",
        "anchor.establish",
        "probe.reply.apply",
        "confusion.reply.apply",
        "confusion.attribution.replay",
        "confusion.open.sync",
    }
    assert set(dialogue_learn_queue.ANCHOR_TRANSITION_POLICY) == set(
        dialogue_learn_queue.DialogueJobKind
    )


async def test_typed_queue_serializes_100_concurrent_mixed_submissions() -> None:
    """Q2: concurrent producers share one FIFO consumer and one total order."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    active = 0
    max_active = 0
    observed_sequences: list[int] = []
    release_producers = asyncio.Event()

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        observed_sequences.append(job.sequence)
        yielded = asyncio.Event()
        asyncio.get_running_loop().call_soon(yielded.set)
        await yielded.wait()
        active -= 1
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    kinds = (
        DialogueJobKind.LEARN,
        DialogueJobKind.CARD_DEFER,
        DialogueJobKind.CONFUSION_REPLY_APPLY,
        DialogueJobKind.CARD_RECONCILE,
    )

    async def submit_one(index: int) -> DialogueJob:
        await release_producers.wait()
        admitted = queue.submit(
            kinds[index % len(kinds)],
            {"index": index},
            completion=True,
        )
        assert admitted is not None
        return admitted

    producers = [asyncio.create_task(submit_one(index)) for index in range(100)]
    release_producers.set()
    jobs = await asyncio.gather(*producers)
    completions = [job.completion for job in jobs]
    assert all(completion is not None for completion in completions)
    await asyncio.gather(
        *(asyncio.shield(completion) for completion in completions if completion is not None)
    )
    await queue.shutdown()

    accepted_sequences = sorted(job.sequence for job in jobs)
    assert max_active == 1
    assert observed_sequences == accepted_sequences
    assert accepted_sequences == list(range(accepted_sequences[0], accepted_sequences[0] + 100))


async def test_declared_entry_kinds_use_real_runtime_handlers_and_guard_f1_f2(
    tmp_path: Path,
) -> None:
    """F5: every declared kind crosses its production handler, including F1/F2."""
    from contextlib import suppress

    from openbiliclaw.api.app import create_app
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine
    from openbiliclaw.soul.identity import insight_hash8

    class Registry:
        async def complete(self, *_args: object, **_kwargs: object) -> LLMResponse:
            return LLMResponse(content="[]", provider="fake")

    memory = MemoryManager(tmp_path / "data")
    memory.initialize()
    engine = SoulEngine(llm=Registry(), memory=memory)  # type: ignore[arg-type]
    app = create_app(
        memory_manager=memory,
        database=memory._database,
        soul_engine=engine,
    )
    ctx = app.state.runtime_context
    queue = ctx.dialogue_settlement_queue
    real_dispatcher = queue._dispatcher
    observed_real_kinds: set[DialogueJobKind] = set()
    b2_entered = asyncio.Event()
    release_b2 = asyncio.Event()

    async def observed_dispatcher(
        job: DialogueJob,
    ) -> DialogueJobResult | DialogueDispatchResult | AnchorMutationTerminal | None:
        if job.payload.get("f1_builder") == "B2":
            b2_entered.set()
            await release_b2.wait()
        if job.payload.get("f2_parent") is True:

            async def rejected_child_reentry() -> None:
                with pytest.raises(DialogueSettlementReentryError):
                    await queue.submit_and_wait(
                        DialogueJobKind.CARD_RECONCILE,
                        {
                            "ref": "f2-child-missing",
                            "turn_id": "",
                            "target_kind": "hypothesis",
                            "target_ref": "f2-child-missing",
                        },
                    )

            await asyncio.create_task(rejected_child_reentry())
        result = await real_dispatcher(job)
        observed_real_kinds.add(job.kind)
        return result

    queue._dispatcher = observed_dispatcher
    queue.start()

    def create_completed_turn(
        turn_id: str,
        *,
        scope: str,
        subject_id: str,
        subject_title: str,
        message: str,
        reply: str,
        payload: dict[str, object],
    ) -> None:
        memory._database.create_chat_turn(
            turn_id=turn_id,
            session="popup",
            scope=scope,
            subject_id=subject_id,
            subject_title=subject_title,
            message=message,
            payload=payload,
        )
        memory._database.complete_chat_turn(turn_id, reply=reply)

    hypothesis = "F5 真实 handler 假设"
    hypothesis_ref = insight_hash8(hypothesis)
    insight = memory.get_layer("insight")
    insight.data["hypotheses"] = [
        {
            "hypothesis": hypothesis,
            "evidence": ["F5"],
            "confidence": 0.72,
            "validated": False,
            "created_at": "2026-07-23",
        }
    ]
    insight.save()
    confusion_settle_id = memory._database.insert_confusion(
        source="awareness",
        topic="F5 结算疑惑",
        observation="需要真实 confusion settlement",
        interpretation_confidence=0.8,
    )
    confusion_open_id = memory._database.insert_confusion(
        source="awareness",
        topic="F5 open 疑惑",
        observation="需要真实 confusion.open.sync",
        interpretation_confidence=0.79,
    )
    create_completed_turn(
        "f5-card-defer",
        scope="hypothesis",
        subject_id="f5-card-defer-ref",
        subject_title="F5 defer",
        message="阿b 的猜测",
        reply="",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": "f5-card-defer-ref",
            "title": "F5 defer",
            "state": "pending",
        },
    )
    create_completed_turn(
        "f5-card-discuss",
        scope="hypothesis",
        subject_id="f5-card-discuss-ref",
        subject_title="F5 discuss",
        message="阿b 的猜测",
        reply="",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": "f5-card-discuss-ref",
            "title": "F5 discuss",
            "state": "pending",
        },
    )
    create_completed_turn(
        "f5-card-reconcile",
        scope="hypothesis",
        subject_id="f5-card-reconcile-ref",
        subject_title="F5 reconcile",
        message="阿b 的猜测",
        reply="",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": "f5-card-reconcile-ref",
            "title": "F5 reconcile",
            "state": "discussing",
        },
    )
    create_completed_turn(
        "f5-probe",
        scope="probe",
        subject_id="f5-probe-domain",
        subject_title="F5 probe",
        message="再看看",
        reply="好的",
        payload={},
    )
    create_completed_turn(
        "f5-confusion-reply",
        scope="confusion",
        subject_id=str(confusion_settle_id),
        subject_title="F5 confusion",
        message="实际情况",
        reply="明白了",
        payload={},
    )

    try:
        b1 = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "f1_builder": "B1",
                "target_kind": "hypothesis",
                "target_ref": "f1-A",
                "origin_turn_id": "f1-turn-A",
                "entry": "pending_open",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        b2 = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "f1_builder": "B2",
                "target_kind": "hypothesis",
                "target_ref": "f1-B",
                "origin_turn_id": "f1-turn-B",
                "entry": "pending_open",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        assert b1 is not None and b1.completion is not None
        assert b2 is not None and b2.completion is not None
        await asyncio.wait_for(b2_entered.wait(), timeout=1)
        latest = queue.registry.snapshot()
        assert isinstance(latest, AnchorReserved)
        assert latest.reservation_id == b2.owned_anchor_reservation_id
        queue.registry.release(latest)
        release_b2.set()
        await asyncio.gather(
            asyncio.shield(b1.completion),
            asyncio.shield(b2.completion),
        )

        f2_result = await asyncio.wait_for(
            queue.submit_and_wait(
                DialogueJobKind.CARD_RECONCILE,
                {
                    "f2_parent": True,
                    "ref": "f2-parent-missing",
                    "turn_id": "",
                    "target_kind": "hypothesis",
                    "target_ref": "f2-parent-missing",
                },
            ),
            timeout=0.5,
        )
        assert f2_result.outcome == "not_found"

        jobs = (
            (
                DialogueJobKind.LEARN,
                {
                    "user_message": "F5 learn",
                    "assistant_reply": "F5 reply",
                    "session": "popup",
                    "scope": "chat",
                    "turn_id": "f5-learn",
                },
            ),
            (
                DialogueJobKind.SETTLE_HYPOTHESIS,
                {
                    "ref": hypothesis_ref,
                    "hypothesis": hypothesis,
                    "requested_verdict": "confirm",
                    "turn_id": "f5-settle-hypothesis",
                    "source": "card_action",
                    "derived": [],
                    "target_kind": "hypothesis",
                    "target_ref": hypothesis_ref,
                },
            ),
            (
                DialogueJobKind.SETTLE_CONFUSION,
                {
                    "ref": str(confusion_settle_id),
                    "requested_verdict": "confirm",
                    "note": "F5",
                    "turn_id": "f5-settle-confusion",
                    "source": "card_action",
                    "target_kind": "confusion",
                    "target_ref": str(confusion_settle_id),
                },
            ),
            (
                DialogueJobKind.CARD_DEFER,
                {
                    "turn_id": "f5-card-defer",
                    "target_kind": "hypothesis",
                    "target_ref": "f5-card-defer-ref",
                },
            ),
            (
                DialogueJobKind.CARD_DISCUSS,
                {
                    "turn_id": "f5-card-discuss",
                    "target_kind": "hypothesis",
                    "target_ref": "f5-card-discuss-ref",
                    "producer_source": "card_action",
                },
            ),
            (
                DialogueJobKind.CARD_RECONCILE,
                {
                    "turn_id": "f5-card-reconcile",
                    "ref": "f5-card-reconcile-ref",
                    "target_kind": "hypothesis",
                    "target_ref": "f5-card-reconcile-ref",
                },
            ),
            (
                DialogueJobKind.PROBE_REPLY_APPLY,
                {
                    "turn_id": "f5-probe",
                    "domain": "f5-probe-domain",
                    "message": "再看看",
                    "reply": "好的",
                },
            ),
            (
                DialogueJobKind.CONFUSION_REPLY_APPLY,
                {
                    "turn_id": "f5-confusion-reply",
                    "subject_id": str(confusion_settle_id),
                    "subject_title": "F5 confusion",
                    "message": "实际情况",
                    "reply": "明白了",
                },
            ),
            (
                DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY,
                {
                    "confusion_id": 999999,
                    "turn_id": "f5-replay-missing",
                    "replay_id": "f5-replay-missing",
                    "has_replay_queue": False,
                    "needs_anchor": False,
                    "target_kind": "confusion",
                    "target_ref": "999999",
                },
            ),
            (
                DialogueJobKind.CONFUSION_OPEN_SYNC,
                {
                    "operation": "schedule",
                    "confusion_id": confusion_open_id,
                    "ask_turn_id": "f5-open-turn",
                    "asked_at": "2026-07-23T00:00:00+00:00",
                    "ignore_cooldown": True,
                },
            ),
        )
        for kind, payload in jobs:
            await asyncio.wait_for(queue.submit_and_wait(kind, payload), timeout=2)

        assert observed_real_kinds == set(DialogueJobKind)
        assert {
            DialogueJobKind(kind) for entry in ENTRY_INVENTORY for kind in entry.job_kinds
        } == observed_real_kinds - {DialogueJobKind.SETTLE_CONFUSION}
    finally:
        release_b2.set()
        with suppress(TimeoutError):
            await queue.shutdown(timeout=0.5)
        memory._database.close()


async def test_known_limit_out_of_scope_writer_is_not_coordinated() -> None:
    """Known limit: an undeclared direct writer remains outside this guarantee."""
    entered = asyncio.Event()
    release = asyncio.Event()
    observations: list[str] = []

    async def dispatcher(_job: DialogueJob) -> DialogueJobResult:
        entered.set()
        await release.wait()
        observations.append("declared-worker")
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    completion = asyncio.create_task(
        queue.submit_and_wait(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {"target_kind": "hypothesis", "target_ref": "known-limit"},
        )
    )
    await entered.wait()
    observations.append("out-of-scope-direct-writer")
    release.set()
    await completion
    await queue.shutdown()

    assert "force_tick" in OUT_OF_SCOPE_WRITERS
    assert observations == ["out-of-scope-direct-writer", "declared-worker"]


async def test_fire_and_forget_and_completion_jobs_share_one_queue() -> None:
    """Q2: learn fire-and-forget and request/response actions use one consumer."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    observed: list[str] = []

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        observed.append(str(job.payload["label"]))
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    learn_job = queue.submit(DialogueJobKind.LEARN, {"label": "learn"})
    assert learn_job is not None
    result = await queue.submit_and_wait(
        DialogueJobKind.CARD_DEFER,
        {"label": "action"},
    )
    await queue.shutdown()

    assert learn_job.completion is None
    assert result.outcome == "completed"
    assert observed == ["learn", "action"]


async def test_completion_failure_does_not_kill_typed_worker() -> None:
    """Q2: an exceptional completion is delivered and later jobs still run."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    observed: list[str] = []

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        label = str(job.payload["label"])
        if label == "boom":
            raise ValueError("typed-dispatch-boom")
        observed.append(label)
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    with pytest.raises(ValueError, match="typed-dispatch-boom"):
        await queue.submit_and_wait(DialogueJobKind.CARD_DEFER, {"label": "boom"})
    first = await queue.submit_and_wait(DialogueJobKind.CARD_DEFER, {"label": "after-1"})
    second = await queue.submit_and_wait(DialogueJobKind.CARD_DEFER, {"label": "after-2"})
    await queue.shutdown()

    assert first.outcome == second.outcome == "completed"
    assert observed == ["after-1", "after-2"]


async def test_typed_envelope_deep_copies_payload_before_enqueue() -> None:
    """Q2: caller mutation after admission cannot alter queued nested payload."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )

    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()
    observed: list[object] = []

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        if job.payload.get("label") == "blocker":
            blocker_entered.set()
            await release_blocker.wait()
        else:
            observed.append(job.payload["nested"])
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    queue.submit(DialogueJobKind.LEARN, {"label": "blocker"})
    await asyncio.wait_for(blocker_entered.wait(), timeout=1)
    payload: dict[str, object] = {"label": "copy", "nested": {"value": ["before"]}}
    copied_job = queue.submit(DialogueJobKind.LEARN, payload, completion=True)
    assert copied_job is not None and copied_job.completion is not None
    nested = payload["nested"]
    assert isinstance(nested, dict)
    values = nested["value"]
    assert isinstance(values, list)
    values.append("after")
    release_blocker.set()
    await asyncio.shield(copied_job.completion)
    await queue.shutdown()

    assert observed == [{"value": ["before"]}]


async def test_submit_and_wait_reentry_fails_fast_and_worker_continues() -> None:
    """Q4/F2: worker code must call internal apply instead of its own queue."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
        DialogueSettlementReentryError,
    )

    observed: list[str] = []
    queue: DialogueSettlementQueue

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        label = str(job.payload["label"])
        if label == "reenter":
            with pytest.raises(DialogueSettlementReentryError):
                queue.submit(
                    DialogueJobKind.CARD_DEFER,
                    {"label": "nested-fire-and-forget"},
                )
            with pytest.raises(DialogueSettlementReentryError):
                await asyncio.wait_for(
                    queue.submit_and_wait(
                        DialogueJobKind.CARD_DEFER,
                        {"label": "nested"},
                    ),
                    timeout=0.1,
                )
        observed.append(label)
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    await queue.submit_and_wait(DialogueJobKind.LEARN, {"label": "reenter"})
    await queue.submit_and_wait(DialogueJobKind.LEARN, {"label": "after"})
    await queue.shutdown()

    assert observed == ["reenter", "after"]


async def test_worker_multilevel_nested_apply_and_child_reentry_do_not_deadlock() -> None:
    """new-1: nested apply stays on worker; multilevel child reentry fails fast."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
        DialogueSettlementReentryError,
    )
    from openbiliclaw.soul.dialogue_settlement_guard import DialogueSettlementGuard

    guard = DialogueSettlementGuard()
    observed: list[str] = []
    tasks: list[asyncio.Task[object] | None] = []
    queue: DialogueSettlementQueue

    async def apply_internal_at_depth(depth: int) -> None:
        tasks.append(asyncio.current_task())
        if depth:
            await apply_internal_at_depth(depth - 1)
            return
        queue.require_dialogue_settlement_worker()
        observed.append("internal-apply")

    async def grandchild_reentry() -> None:
        tasks.append(asyncio.current_task())
        with pytest.raises(DialogueSettlementReentryError):
            await asyncio.wait_for(
                queue.submit_and_wait(
                    DialogueJobKind.CARD_DEFER,
                    {"label": "must-not-enqueue"},
                ),
                timeout=0.1,
            )

    async def child_reentry() -> None:
        tasks.append(asyncio.current_task())
        await asyncio.create_task(grandchild_reentry())

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        queue.require_dialogue_settlement_worker()
        label = str(job.payload["label"])
        tasks.append(asyncio.current_task())
        if label == "parent":
            await apply_internal_at_depth(3)
            await asyncio.create_task(child_reentry())
        observed.append(label)
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher, guard=guard)
    queue.start()
    worker_task = queue.worker_task
    assert worker_task is not None
    try:
        result = await asyncio.wait_for(
            queue.submit_and_wait(DialogueJobKind.LEARN, {"label": "parent"}),
            timeout=0.5,
        )
        assert result.outcome == "completed"
        await asyncio.wait_for(queue.pause_and_drain(timeout=0.5), timeout=1)
    finally:
        await queue.shutdown(timeout=0.5)

    assert observed == ["internal-apply", "parent"]
    assert tasks[:5] == [worker_task] * 5
    assert all(task is not worker_task for task in tasks[5:])
    assert queue.depth == 0


async def test_typed_worker_permit_rejects_inherited_child_context() -> None:
    """Q14/F4: queue dispatch owns the permit; a child task never inherits it."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
    )
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementGuard,
        DialogueSettlementMutationOutsideWorker,
    )

    guard = DialogueSettlementGuard()
    mutations: list[str] = []

    def protected_mutator(label: str) -> None:
        guard.require_dialogue_settlement_worker()
        mutations.append(label)

    async def dispatcher(_job: DialogueJob) -> DialogueJobResult:
        protected_mutator("worker")

        async def child() -> None:
            protected_mutator("child")

        child_task = asyncio.create_task(child())
        with pytest.raises(DialogueSettlementMutationOutsideWorker):
            await child_task
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher, guard=guard)
    queue.start()
    await queue.submit_and_wait(DialogueJobKind.LEARN, {"label": "guard"})
    await queue.shutdown()

    assert mutations == ["worker"]


async def test_detached_child_loses_write_and_reentry_rights_before_next_job() -> None:
    """new-2/M7: an old child stays denied after its parent job and during the next."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueJobKind,
        DialogueJobResult,
        DialogueSettlementQueue,
        DialogueSettlementReentryError,
    )
    from openbiliclaw.soul.dialogue_settlement_guard import (
        DialogueSettlementGuard,
        DialogueSettlementMutationOutsideWorker,
    )

    guard = DialogueSettlementGuard()
    child_checked_reentry = asyncio.Event()
    next_job_started = asyncio.Event()
    mutations: list[str] = []
    stale_child: asyncio.Task[None] | None = None
    queue: DialogueSettlementQueue

    def protected_mutator(label: str) -> None:
        guard.require_dialogue_settlement_worker()
        mutations.append(label)

    async def detached_child() -> None:
        # On the retired inline-delegation design this call temporarily
        # authorized this exact child task. Keeping that authorization after
        # return is the M7 mutation this expiry probe must catch.
        with pytest.raises(DialogueSettlementReentryError):
            await asyncio.wait_for(
                queue.submit_and_wait(
                    DialogueJobKind.CARD_DEFER,
                    {"label": "old-child-reentry"},
                ),
                timeout=0.1,
            )
        child_checked_reentry.set()
        await next_job_started.wait()
        with pytest.raises(DialogueSettlementMutationOutsideWorker):
            protected_mutator("stale-child")
        with pytest.raises(DialogueSettlementReentryError):
            queue.submit(
                DialogueJobKind.CARD_DEFER,
                {"label": "stale-child-submit"},
            )

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        nonlocal stale_child
        label = str(job.payload["label"])
        protected_mutator(label)
        if label == "parent":
            stale_child = asyncio.create_task(detached_child())
            await child_checked_reentry.wait()
        elif label == "next":
            next_job_started.set()
            assert stale_child is not None
            await stale_child
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher, guard=guard)
    queue.start()
    try:
        parent = await asyncio.wait_for(
            queue.submit_and_wait(DialogueJobKind.LEARN, {"label": "parent"}),
            timeout=0.5,
        )
        assert parent.outcome == "completed"
        following = await asyncio.wait_for(
            queue.submit_and_wait(DialogueJobKind.LEARN, {"label": "next"}),
            timeout=0.5,
        )
        assert following.outcome == "completed"
    finally:
        await queue.shutdown(timeout=0.5)

    assert mutations == ["parent", "next"]
    assert queue.depth == 0


async def test_three_anchor_admission_timelines_repeat_100_times_without_mismatch() -> None:
    """Q3/F2/R2-1: establish, discuss, and absent-first timelines stay linear."""
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()

    async def dispatcher(
        job: DialogueJob,
    ) -> DialogueJobResult | AnchorMutationTerminal:
        if job.payload.get("label") == "blocker":
            blocker_entered.set()
            await release_blocker.wait()
            return DialogueJobResult(outcome="completed")
        if job.owned_anchor_reservation_id is not None:
            transition = job.anchor_transition
            return AnchorMutationTerminal.persisted(
                kind=transition.target_kind,
                ref=transition.target_ref,
                generation=job.sequence + 1,
            )
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    queue.submit(DialogueJobKind.LEARN, {"label": "blocker"})
    await asyncio.wait_for(blocker_entered.wait(), timeout=1)
    try:
        for iteration in range(100):
            establish_ref = f"establish-{iteration}"
            establish = queue.submit(
                DialogueJobKind.ANCHOR_ESTABLISH,
                {
                    "target_kind": "hypothesis",
                    "target_ref": establish_ref,
                    "producer_source": "durable_confusion_ensure",
                },
            )
            establish_settle = queue.submit(
                DialogueJobKind.SETTLE_HYPOTHESIS,
                {
                    "target_kind": "hypothesis",
                    "target_ref": establish_ref,
                },
            )
            assert establish is not None and establish_settle is not None
            assert isinstance(establish_settle.anchor_snapshot, AnchorReserved)
            assert (
                establish_settle.anchor_snapshot.reservation_id
                == establish.owned_anchor_reservation_id
            )

            discuss_ref = f"discuss-{iteration}"
            discuss = queue.submit(
                DialogueJobKind.CARD_DISCUSS,
                {
                    "target_kind": "hypothesis",
                    "target_ref": discuss_ref,
                },
            )
            discuss_settle = queue.submit(
                DialogueJobKind.SETTLE_HYPOTHESIS,
                {
                    "target_kind": "hypothesis",
                    "target_ref": discuss_ref,
                },
            )
            assert discuss is not None and discuss_settle is not None
            assert isinstance(discuss_settle.anchor_snapshot, AnchorReserved)
            assert (
                discuss_settle.anchor_snapshot.reservation_id == discuss.owned_anchor_reservation_id
            )

            absent_ref = f"absent-first-{iteration}"
            absent_settle = queue.submit(
                DialogueJobKind.SETTLE_HYPOTHESIS,
                {
                    "target_kind": "hypothesis",
                    "target_ref": absent_ref,
                },
            )
            later_builder = queue.submit(
                DialogueJobKind.ANCHOR_ESTABLISH,
                {
                    "target_kind": "hypothesis",
                    "target_ref": absent_ref,
                    "producer_source": "pending_probe_throw",
                },
            )
            assert absent_settle is not None and later_builder is not None
            assert isinstance(absent_settle.anchor_snapshot, AnchorAbsent)
            assert absent_settle.anchor_snapshot.target_ref == absent_ref
        release_blocker.set()
        await asyncio.wait_for(queue.shutdown(), timeout=5)
    finally:
        release_blocker.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)


def test_cross_ref_older_builder_resolution_preserves_latest_reserved_head() -> None:
    """F1: resolving B1(A) cannot move the global latest head behind B2(B)."""
    from openbiliclaw.soul.dialogue_learn_queue import AnchorAdmissionRegistry

    registry = AnchorAdmissionRegistry()
    b1 = registry.reserve(
        kind="hypothesis",
        ref="A",
        owner_job_id="B1",
        owner_sequence=1,
        producer_kind=DialogueJobKind.ANCHOR_ESTABLISH.value,
        origin="durable_confusion_ensure",
    )
    b2 = registry.reserve(
        kind="hypothesis",
        ref="B",
        owner_job_id="B2",
        owner_sequence=2,
        producer_kind=DialogueJobKind.ANCHOR_ESTABLISH.value,
        origin="durable_confusion_ensure",
    )

    registry.resolve_owned(
        ref="A",
        reservation_id=b1.reservation_id,
        owner_job_id=b1.owner_job_id,
        owner_sequence=b1.owner_sequence,
        terminal=AnchorMutationTerminal.persisted(
            kind="hypothesis",
            ref="A",
            generation=1,
        ),
    )

    latest = registry.snapshot()
    assert latest == b2
    registry.release(latest)


async def test_old_non_builder_refresh_cannot_replace_new_cross_ref_reservation() -> None:
    """new-3: old A release completes after B admission without moving latest off B."""
    active: dict[str, object] = {
        "anchor_kind": "hypothesis",
        "anchor_ref": "A",
        "anchor_generation": 1,
    }
    old_effect_applied = asyncio.Event()
    release_old_completion = asyncio.Event()
    builder_b_entered = asyncio.Event()
    release_builder_b = asyncio.Event()

    async def dispatcher(
        job: DialogueJob,
    ) -> DialogueJobResult | AnchorMutationTerminal:
        label = str(job.payload["label"])
        if label == "old-A-release":
            active.clear()
            old_effect_applied.set()
            await release_old_completion.wait()
            return DialogueJobResult(outcome="applied")
        assert label == "new-B-builder"
        builder_b_entered.set()
        await release_builder_b.wait()
        active.update(
            anchor_kind="hypothesis",
            anchor_ref="B",
            anchor_generation=2,
        )
        return AnchorMutationTerminal.persisted(
            kind="hypothesis",
            ref="B",
            generation=2,
        )

    queue = DialogueSettlementQueue(dispatcher, anchor_provider=lambda: dict(active))
    queue.start()
    try:
        old_a = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "label": "old-A-release",
                "target_kind": "hypothesis",
                "target_ref": "A",
            },
            completion=True,
        )
        assert old_a is not None and old_a.completion is not None
        await asyncio.wait_for(old_effect_applied.wait(), timeout=0.5)
        new_b = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "label": "new-B-builder",
                "target_kind": "hypothesis",
                "target_ref": "B",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        assert new_b is not None and new_b.completion is not None
        assert new_b.owned_anchor_reservation_id is not None

        release_old_completion.set()
        await asyncio.wait_for(builder_b_entered.wait(), timeout=0.5)
        assert (await asyncio.shield(old_a.completion)).outcome == "applied"

        latest = queue.registry.snapshot()
        assert isinstance(latest, AnchorReserved)
        assert latest.ref == "B"
        assert latest.reservation_id == new_b.owned_anchor_reservation_id
        queue.registry.release(latest)

        release_builder_b.set()
        assert (await asyncio.shield(new_b.completion)).outcome == "persisted"
    finally:
        release_old_completion.set()
        release_builder_b.set()
        await queue.shutdown(timeout=0.5)


async def test_targetless_learn_refresh_preserves_new_cross_ref_reservation() -> None:
    """F1/F2: inferred A refresh cannot replace a later B reservation."""
    active: dict[str, object] = {
        "anchor_kind": "hypothesis",
        "anchor_ref": "A",
        "anchor_generation": 1,
    }
    old_effect_applied = asyncio.Event()
    release_old_completion = asyncio.Event()
    builder_b_entered = asyncio.Event()
    release_builder_b = asyncio.Event()

    async def dispatcher(
        job: DialogueJob,
    ) -> DialogueJobResult | AnchorMutationTerminal:
        label = str(job.payload["label"])
        if label == "targetless-learn-A-release":
            active.clear()
            old_effect_applied.set()
            await release_old_completion.wait()
            return DialogueJobResult(outcome="completed")
        builder_b_entered.set()
        await release_builder_b.wait()
        active.update(
            anchor_kind="hypothesis",
            anchor_ref="B",
            anchor_generation=2,
        )
        return AnchorMutationTerminal.persisted(
            kind="hypothesis",
            ref="B",
            generation=2,
        )

    queue = DialogueSettlementQueue(dispatcher, anchor_provider=lambda: dict(active))
    queue.start()
    try:
        old_a = queue.submit(
            DialogueJobKind.LEARN,
            {"label": "targetless-learn-A-release"},
            completion=True,
        )
        assert old_a is not None and old_a.completion is not None
        assert isinstance(old_a.anchor_snapshot, AnchorPersisted)
        await asyncio.wait_for(old_effect_applied.wait(), timeout=0.5)

        new_b = queue.submit(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "label": "new-B-builder",
                "target_kind": "hypothesis",
                "target_ref": "B",
                "producer_source": "durable_confusion_ensure",
            },
            completion=True,
        )
        assert new_b is not None and new_b.completion is not None
        assert new_b.owned_anchor_reservation_id is not None

        release_old_completion.set()
        await asyncio.wait_for(builder_b_entered.wait(), timeout=0.5)
        assert (await asyncio.shield(old_a.completion)).outcome == "completed"

        latest = queue.registry.snapshot()
        assert isinstance(latest, AnchorReserved)
        assert latest.ref == "B"
        assert latest.reservation_id == new_b.owned_anchor_reservation_id
        queue.registry.release(latest)

        release_builder_b.set()
        assert (await asyncio.shield(new_b.completion)).outcome == "persisted"
    finally:
        release_old_completion.set()
        release_builder_b.set()
        await queue.shutdown(timeout=0.5)


def test_owner_cas_failed_gc_and_double_builder_state_machines_repeat_100_times() -> None:
    """Q18/Q19: owner checks, failed GC, and later-head protection are stable."""
    from openbiliclaw.soul.dialogue_learn_queue import (
        AnchorAdmissionRegistry,
        AnchorReservationResolutionError,
    )

    registry = AnchorAdmissionRegistry()
    for iteration in range(100):
        target_ref = f"double-{iteration}"
        b1 = registry.reserve(
            kind="hypothesis",
            ref=target_ref,
            owner_job_id=f"B1-{iteration}",
            owner_sequence=iteration * 2 + 1,
            producer_kind=DialogueJobKind.ANCHOR_ESTABLISH.value,
            origin="durable_confusion_ensure",
        )
        b1_snapshot = registry.snapshot(
            target_kind="hypothesis",
            target_ref=target_ref,
        )
        b2 = registry.reserve(
            kind="hypothesis",
            ref=target_ref,
            owner_job_id=f"B2-{iteration}",
            owner_sequence=iteration * 2 + 2,
            producer_kind=DialogueJobKind.ANCHOR_ESTABLISH.value,
            origin="durable_confusion_ensure",
        )
        b2_snapshot = registry.snapshot(
            target_kind="hypothesis",
            target_ref=target_ref,
        )
        dependent = registry.snapshot(
            target_kind="hypothesis",
            target_ref=target_ref,
        )
        with pytest.raises(AnchorReservationResolutionError):
            registry.resolve_owned(
                ref=target_ref,
                reservation_id=b1.reservation_id,
                owner_job_id=b2.owner_job_id,
                owner_sequence=b2.owner_sequence,
                terminal=AnchorMutationTerminal.persisted(
                    kind="hypothesis",
                    ref=target_ref,
                    generation=iteration + 1,
                ),
            )
        registry.resolve_owned(
            ref=target_ref,
            reservation_id=b1.reservation_id,
            owner_job_id=b1.owner_job_id,
            owner_sequence=b1.owner_sequence,
            terminal=AnchorMutationTerminal.persisted(
                kind="hypothesis",
                ref=target_ref,
                generation=iteration + 1,
            ),
        )
        assert (
            registry.head(
                target_kind="hypothesis",
                target_ref=target_ref,
            )
            == b2
        )
        registry.resolve_owned(
            ref=target_ref,
            reservation_id=b2.reservation_id,
            owner_job_id=b2.owner_job_id,
            owner_sequence=b2.owner_sequence,
            terminal=AnchorMutationTerminal.no_op(
                AnchorPersisted("hypothesis", target_ref, iteration + 1)
            ),
        )
        with pytest.raises(AnchorReservationResolutionError):
            registry.resolve_owned(
                ref=target_ref,
                reservation_id=b2.reservation_id,
                owner_job_id=b2.owner_job_id,
                owner_sequence=b2.owner_sequence,
                terminal=AnchorMutationTerminal.no_op(
                    AnchorPersisted("hypothesis", target_ref, iteration + 1)
                ),
            )
        later = registry.snapshot(
            target_kind="hypothesis",
            target_ref=target_ref,
        )
        assert isinstance(later, AnchorPersisted)
        assert later.resolved_by_reservation_id == b2.reservation_id
        registry.release(b1_snapshot)
        registry.release(b2_snapshot)
        registry.release(dependent)
        assert not registry.has_reservation(b1.reservation_id)
        assert not registry.has_reservation(b2.reservation_id)

        failed_ref = f"failed-{iteration}"
        failed = registry.reserve(
            kind="hypothesis",
            ref=failed_ref,
            owner_job_id=f"failed-owner-{iteration}",
            owner_sequence=1000 + iteration,
            producer_kind=DialogueJobKind.ANCHOR_ESTABLISH.value,
            origin="durable_confusion_ensure",
        )
        failed_owner = registry.snapshot(
            target_kind="hypothesis",
            target_ref=failed_ref,
        )
        old_one = registry.snapshot(
            target_kind="hypothesis",
            target_ref=failed_ref,
        )
        old_two = registry.snapshot(
            target_kind="hypothesis",
            target_ref=failed_ref,
        )
        registry.resolve_owned(
            ref=failed_ref,
            reservation_id=failed.reservation_id,
            owner_job_id=failed.owner_job_id,
            owner_sequence=failed.owner_sequence,
            terminal=AnchorMutationTerminal.failed(
                AnchorAbsent("hypothesis", failed_ref, 1),
                cause="controlled",
            ),
        )
        new_snapshot = registry.snapshot(
            target_kind="hypothesis",
            target_ref=failed_ref,
        )
        assert isinstance(new_snapshot, AnchorAbsent)
        assert registry.reservation_reference_count(failed.reservation_id) == 3
        registry.release(failed_owner)
        registry.release(old_one)
        registry.release(old_two)
        assert not registry.has_reservation(failed.reservation_id)
        retry = registry.reserve(
            kind="hypothesis",
            ref=failed_ref,
            owner_job_id=f"retry-owner-{iteration}",
            owner_sequence=2000 + iteration,
            producer_kind=DialogueJobKind.ANCHOR_ESTABLISH.value,
            origin="durable_confusion_ensure",
        )
        assert retry.reservation_id != failed.reservation_id
        retry_owner = registry.snapshot(
            target_kind="hypothesis",
            target_ref=failed_ref,
        )
        registry.resolve_owned(
            ref=failed_ref,
            reservation_id=retry.reservation_id,
            owner_job_id=retry.owner_job_id,
            owner_sequence=retry.owner_sequence,
            terminal=AnchorMutationTerminal.persisted(
                kind="hypothesis",
                ref=failed_ref,
                generation=iteration + 1,
            ),
        )
        registry.release(retry_owner)
        assert not registry.has_reservation(retry.reservation_id)


async def test_builder_timeout_resolves_failed_and_releases_all_old_references() -> None:
    """Q18/R2-2: timeout cannot leave a reserved entry or run old effects."""
    old_effects: list[str] = []

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        if job.owned_anchor_reservation_id is not None:
            raise TimeoutError("controlled anchor timeout")
        old_effects.append(str(job.payload["label"]))
        return DialogueJobResult(outcome="applied")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    builder = queue.submit(
        DialogueJobKind.ANCHOR_ESTABLISH,
        {
            "label": "builder",
            "target_kind": "hypothesis",
            "target_ref": "timeout-ref",
            "producer_source": "durable_confusion_ensure",
        },
        completion=True,
    )
    old_dependency = queue.submit(
        DialogueJobKind.SETTLE_HYPOTHESIS,
        {
            "label": "old",
            "target_kind": "hypothesis",
            "target_ref": "timeout-ref",
        },
        completion=True,
    )
    assert builder is not None and old_dependency is not None
    reservation_id = builder.owned_anchor_reservation_id
    assert reservation_id is not None
    assert builder.completion is not None and old_dependency.completion is not None
    with pytest.raises(TimeoutError, match="controlled anchor timeout"):
        await asyncio.shield(builder.completion)
    dependent_result = await asyncio.shield(old_dependency.completion)
    assert dependent_result.outcome == "anchor_dependency_failed"
    assert old_effects == []
    assert not queue.registry.has_reservation(reservation_id)
    await queue.shutdown()


async def test_queue_reload_handoff_old_finally_cannot_clear_new_permit() -> None:
    """Q14/R2-3: queue lifecycle uses revoke/register/compare-and-clear."""
    from openbiliclaw.soul.dialogue_settlement_guard import DialogueSettlementGuard

    guard = DialogueSettlementGuard()
    mutations: list[str] = []

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        guard.require_dialogue_settlement_worker()
        mutations.append(str(job.payload["label"]))
        return DialogueJobResult(outcome="completed")

    old = DialogueSettlementQueue(dispatcher, guard=guard, name="old-settlement-worker")
    old.start()
    await old.submit_and_wait(DialogueJobKind.LEARN, {"label": "old"})
    await old.pause_and_drain()
    old_permit = old.worker_permit
    assert old_permit is not None
    assert old.revoke_worker_permit() is True
    assert guard.registered_permit is None

    new = DialogueSettlementQueue(dispatcher, guard=guard, name="new-settlement-worker")
    new.start()
    await new.submit_and_wait(DialogueJobKind.LEARN, {"label": "new-before-finally"})
    new_permit = new.worker_permit
    assert new_permit is not None and guard.is_current(new_permit)
    await old.shutdown()
    assert guard.is_current(new_permit)
    await new.submit_and_wait(DialogueJobKind.LEARN, {"label": "new-after-finally"})
    await new.shutdown()

    assert mutations == ["old", "new-before-finally", "new-after-finally"]
    assert guard.registered_permit is None


async def test_pause_and_drain_keeps_accepting_until_atomic_idle_pause() -> None:
    """A hot reload must not drop an interactive command while LLM work drains."""
    entered = asyncio.Event()
    release = asyncio.Event()
    observed: list[str] = []

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        label = str(job.payload["label"])
        if label == "slow":
            entered.set()
            await release.wait()
        observed.append(label)
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher)
    queue.start()
    slow = queue.submit(DialogueJobKind.LEARN, {"label": "slow"}, completion=True)
    assert slow is not None and slow.completion is not None
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert queue.ready_for_interactive_submission is False

    draining = asyncio.create_task(queue.pause_and_drain(timeout=1))
    await asyncio.sleep(0)
    late = queue.submit(DialogueJobKind.CARD_DEFER, {"label": "late"}, completion=True)
    assert late is not None and late.completion is not None
    release.set()
    await asyncio.wait_for(draining, timeout=1)

    assert observed == ["slow", "late"]
    assert queue.accepting is False
    assert queue.ready_for_interactive_submission is False
    assert queue.submit(DialogueJobKind.CARD_DEFER, {"label": "rejected"}) is None
    await queue.shutdown()


async def test_queue_reload_start_registers_actual_worker_permit_before_publish() -> None:
    """R2-3: start returns only after the created Task owns the single permit."""
    from openbiliclaw.soul.dialogue_settlement_guard import DialogueSettlementGuard

    guard = DialogueSettlementGuard()

    async def dispatcher(_job: DialogueJob) -> DialogueJobResult:
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher, guard=guard)
    queue.start()

    permit = queue.worker_permit
    assert permit is not None
    assert queue.worker_task is permit.worker_task
    assert guard.is_current(permit)

    await queue.shutdown()


def test_dialogue_llm_dispatcher_boundary_is_typed_and_inline() -> None:
    """Task 1.3: the runtime dispatcher is typed and owns the direct await."""
    import inspect

    from openbiliclaw.api.runtime_context import _build_dialogue_settlement_dispatcher

    assert _build_dialogue_settlement_dispatcher.__annotations__["return"] == "DialogueDispatcher"
    source = inspect.getsource(_build_dialogue_settlement_dispatcher)
    assert "await soul_engine.learn_from_dialogue" in source
    assert "create_task" not in source
    assert "wait_for" not in source


async def test_dialogue_llm_jobs_are_serial_while_heartbeat_stays_responsive(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Task 1.3: blocked LLM work stays in one worker without blocking the loop."""
    import logging

    from openbiliclaw.api.runtime_context import _build_dialogue_settlement_dispatcher

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
            self.release_first = asyncio.Event()
            self.worker_tasks: list[asyncio.Task[object] | None] = []
            self.force_tick_calls = 0
            self.exploration_calls = 0
            self.openclaw_dispatch_calls = 0

        async def learn_from_dialogue(self, **payload: object) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.worker_tasks.append(asyncio.current_task())
            try:
                if payload["user_message"] == "first":
                    self.first_started.set()
                    await self.release_first.wait()
                else:
                    self.second_started.set()
                    await asyncio.sleep(0)
            finally:
                self.active -= 1

        async def force_tick(self) -> None:
            self.force_tick_calls += 1

        async def exploration(self) -> None:
            self.exploration_calls += 1

        async def dispatch_openclaw(self) -> None:
            self.openclaw_dispatch_calls += 1

    fake_soul = FakeSoulEngine()
    queue = DialogueSettlementQueue(_build_dialogue_settlement_dispatcher(fake_soul))
    caplog.set_level(
        logging.DEBUG,
        logger="openbiliclaw.soul.dialogue_learn_queue",
    )
    queue.start()
    worker_task = queue.worker_task
    assert worker_task is not None
    first = queue.submit(
        DialogueJobKind.LEARN,
        {
            "user_message": "first",
            "assistant_reply": "one",
            "session": "popup",
            "scope": "chat",
            "turn_id": "turn-1",
        },
        completion=True,
    )
    second = queue.submit(
        DialogueJobKind.LEARN,
        {
            "user_message": "second",
            "assistant_reply": "two",
            "session": "popup",
            "scope": "chat",
            "turn_id": "turn-2",
        },
        completion=True,
    )
    assert first is not None and first.completion is not None
    assert second is not None and second.completion is not None

    try:
        await asyncio.wait_for(fake_soul.first_started.wait(), timeout=1)
        assert not fake_soul.second_started.is_set()

        heartbeat_ticks = 0
        loop = asyncio.get_running_loop()
        heartbeat_deadline = loop.time() + 0.5
        while loop.time() < heartbeat_deadline:
            heartbeat_ticks += 1
            await asyncio.sleep(0.02)

        assert heartbeat_ticks >= 10
        assert not fake_soul.second_started.is_set()
        fake_soul.release_first.set()
        first_result, second_result = await asyncio.gather(
            asyncio.shield(first.completion),
            asyncio.shield(second.completion),
        )
        assert first_result.outcome == "completed"
        assert second_result.outcome == "completed"
    finally:
        fake_soul.release_first.set()
        await queue.shutdown()

    assert fake_soul.max_active == 1
    assert fake_soul.worker_tasks == [worker_task, worker_task]
    assert fake_soul.force_tick_calls == 0
    assert fake_soul.exploration_calls == 0
    assert fake_soul.openclaw_dispatch_calls == 0
    timing_records = [
        record for record in caplog.records if record.message == "dialogue settlement job"
    ]
    assert len(timing_records) == 2
    assert all(record.queue_wait_ms >= 0 for record in timing_records)
    assert all(record.run_ms >= 0 for record in timing_records)
    assert timing_records[1].queue_wait_ms > 0


def test_dialogue_serial_observability_thresholds_are_documented() -> None:
    """Task 1.3: production split thresholds live in docs, not a second queue."""
    module_doc = (Path(__file__).parents[1] / "docs/modules/soul.md").read_text(encoding="utf-8")

    assert "`202 ratio >1%`" in module_doc
    assert "`p95 >5s`" in module_doc
    assert "不预埋第二队列" in module_doc


def test_runtime_uses_one_settlement_queue_and_two_legacy_direct_callsites() -> None:
    """F5/R2-3: runtime naming is singular and production direct mode is allowlisted."""
    import ast
    import inspect

    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.soul import dialogue

    fields = RuntimeContext.__dataclass_fields__
    assert "dialogue_settlement_queue" in fields
    assert "dialogue_learn_queue" not in fields

    root = Path(__file__).parents[1]
    production_sources = {
        path: path.read_text(encoding="utf-8")
        for path in (
            root / "src/openbiliclaw/api/runtime_context.py",
            root / "src/openbiliclaw/cli.py",
            root / "src/openbiliclaw/integrations/openclaw/operations.py",
        )
    }
    direct_token = "DialogueLearningMode.LEGACY_DIRECT"
    assert sum(source.count(direct_token) for source in production_sources.values()) == 2
    assert direct_token not in production_sources[root / "src/openbiliclaw/api/runtime_context.py"]
    legacy_direct_callsites: list[str] = []
    for path, source in production_sources.items():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if (
                    keyword.arg == "learning_mode"
                    and isinstance(value, ast.Attribute)
                    and value.attr == "LEGACY_DIRECT"
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "DialogueLearningMode"
                ):
                    legacy_direct_callsites.append(path.relative_to(root).as_posix())
    assert sorted(legacy_direct_callsites) == [
        "src/openbiliclaw/cli.py",
        "src/openbiliclaw/integrations/openclaw/operations.py",
    ]
    retired_class_name = "Dialogue" + "LearnQueue"
    assert retired_class_name not in "\n".join(production_sources.values())
    assert "if self._learn_queue is not None" not in inspect.getsource(
        dialogue.SocraticDialogue.respond
    )


async def test_runtime_reload_handoff_repeats_with_one_authorized_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2-3: ten reloads register new only after exact old revocation."""
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config
    from openbiliclaw.soul.dialogue_settlement_guard import DialogueSettlementGuard

    for iteration in range(10):
        guard = DialogueSettlementGuard()
        mutations: list[str] = []

        async def dispatcher(
            job: DialogueJob,
            guard: DialogueSettlementGuard = guard,
            mutations: list[str] = mutations,
        ) -> DialogueJobResult:
            guard.require_dialogue_settlement_worker()
            mutations.append(str(job.payload["label"]))
            return DialogueJobResult(outcome="completed")

        old = DialogueSettlementQueue(
            dispatcher,
            guard=guard,
            name=f"runtime-old-{iteration}",
        )
        old.start()
        await old.submit_and_wait(DialogueJobKind.LEARN, {"label": "old"})
        old_permit = old.worker_permit
        assert old_permit is not None
        ctx = RuntimeContext(dialogue_settlement_queue=old)
        built: list[DialogueSettlementQueue] = []

        def rebuild(
            _config: object,
            guard: DialogueSettlementGuard = guard,
            iteration: int = iteration,
            built: list[DialogueSettlementQueue] = built,
            ctx: RuntimeContext = ctx,
        ) -> None:
            new = DialogueSettlementQueue(
                dispatcher,
                guard=guard,
                name=f"runtime-new-{iteration}",
            )
            new.start()
            built.append(new)
            ctx.dialogue_settlement_queue = new

        monkeypatch.setattr(ctx, "_rebuild_components", rebuild)
        await ctx.rebuild_from_config(Config())

        new = built[0]
        new_permit = new.worker_permit
        assert new_permit is not None
        assert new_permit.lifecycle_nonce != old_permit.lifecycle_nonce
        assert guard.is_current(new_permit)
        assert old.worker_alive is False
        await new.submit_and_wait(DialogueJobKind.LEARN, {"label": "new"})
        await new.shutdown()
        assert mutations == ["old", "new"]
        assert guard.registered_permit is None


async def test_runtime_reload_failure_restores_old_with_fresh_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2-3: ten failed rebuilds reauthorize only the drained old Task."""
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config
    from openbiliclaw.soul.dialogue_settlement_guard import DialogueSettlementGuard

    for iteration in range(10):
        guard = DialogueSettlementGuard()
        mutations: list[str] = []

        async def dispatcher(
            job: DialogueJob,
            guard: DialogueSettlementGuard = guard,
            mutations: list[str] = mutations,
        ) -> DialogueJobResult:
            guard.require_dialogue_settlement_worker()
            mutations.append(str(job.payload["label"]))
            return DialogueJobResult(outcome="completed")

        old = DialogueSettlementQueue(
            dispatcher,
            guard=guard,
            name=f"rollback-old-{iteration}",
        )
        old.start()
        await old.submit_and_wait(DialogueJobKind.LEARN, {"label": "before"})
        original_permit = old.worker_permit
        assert original_permit is not None
        ctx = RuntimeContext(dialogue_settlement_queue=old)

        def fail_rebuild(_config: object) -> None:
            raise RuntimeError("controlled rebuild failure")

        monkeypatch.setattr(ctx, "_rebuild_components", fail_rebuild)
        with pytest.raises(RuntimeError, match="controlled rebuild failure"):
            await ctx.rebuild_from_config(Config())

        restored_permit = old.worker_permit
        assert ctx.dialogue_settlement_queue is old
        assert restored_permit is not None
        assert restored_permit.lifecycle_nonce != original_permit.lifecycle_nonce
        assert guard.is_current(restored_permit)
        await old.submit_and_wait(DialogueJobKind.LEARN, {"label": "after"})
        await old.shutdown()
        assert mutations == ["before", "after"]
        assert guard.registered_permit is None
