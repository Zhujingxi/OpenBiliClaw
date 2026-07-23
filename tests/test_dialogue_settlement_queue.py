"""Wave 0 RED contracts for the dialogue settlement queue.

These tests intentionally exercise the legacy ``DialogueLearnQueue`` and
settlement fence.  Wave 0 freezes the missing contracts without implementing
the typed queue/registry planned for Wave 1.

Spec: docs/plans/2026-07-23-dialogue-settlement-queue-spec.md
Plan: docs/plans/2026-07-23-dialogue-settlement-queue-plan.md Task 0.1.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from openbiliclaw.soul.dialogue_learn_queue import DialogueLearnQueue
from openbiliclaw.storage.database import Database

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


async def _capture_legacy_admissions(
    jobs: list[dict[str, object]],
    *,
    dispatch: _DispatchHook,
) -> dict[str, dict[str, object]]:
    """Queue jobs behind a barrier and return their immutable legacy copies."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    observed: dict[str, dict[str, object]] = {}
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()

    async def handler(**payload: object) -> None:
        record = dict(payload)
        job_id = str(record.get("job_id", ""))
        observed[job_id] = record
        if job_id == "wave0-blocker":
            blocker_entered.set()
            await release_blocker.wait()
            return
        await dispatch(record, persisted)

    queue = DialogueLearnQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        assert await queue.submit({"job_id": "wave0-blocker", "kind": "learn"})
        await asyncio.wait_for(blocker_entered.wait(), timeout=1)
        for job in jobs:
            assert await queue.submit(job)
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


@pytest.mark.xfail(strict=True, reason="[Q5] Wave 2 removes the blocking settlement fence")
async def test_file_fence_does_not_block_event_loop_while_owner_awaits(
    tmp_path: Path,
) -> None:
    """Q5: a competing settlement owner must not stop the event-loop heartbeat."""
    database = Database(tmp_path / "settlement.db")
    owner_entered = threading.Event()
    release_owner = threading.Event()
    contender_entered_sync_lock = threading.Event()
    heartbeat_progressed = threading.Event()
    owner_errors: list[BaseException] = []
    heartbeat_before_release: list[bool] = []

    def hold_fence() -> None:
        try:
            with database._card_settlement_fence():
                owner_entered.set()
                if not release_owner.wait(timeout=2):
                    raise TimeoutError("owner fence release timed out")
        except BaseException as exc:  # pragma: no cover - surfaced below
            owner_errors.append(exc)
            owner_entered.set()

    owner = threading.Thread(target=hold_fence, name="wave0-settlement-owner")
    owner.start()
    assert await asyncio.to_thread(owner_entered.wait, 1)
    assert owner_errors == []

    contender_ready = asyncio.Event()
    contender_go = asyncio.Event()

    async def heartbeat() -> None:
        await contender_ready.wait()
        contender_go.set()
        await asyncio.sleep(0)
        heartbeat_progressed.set()

    async def contend_for_fence() -> None:
        contender_ready.set()
        await contender_go.wait()
        contender_entered_sync_lock.set()
        with database._card_settlement_fence():
            pass

    def watchdog() -> None:
        if not contender_entered_sync_lock.wait(timeout=1):
            heartbeat_before_release.append(False)
        else:
            heartbeat_before_release.append(heartbeat_progressed.wait(timeout=0.2))
        release_owner.set()

    watcher = threading.Thread(target=watchdog, name="wave0-settlement-watchdog")
    watcher.start()
    try:
        await asyncio.wait_for(
            asyncio.gather(heartbeat(), contend_for_fence()),
            timeout=2,
        )
    finally:
        release_owner.set()
        await asyncio.to_thread(owner.join, 1)
        await asyncio.to_thread(watcher.join, 1)

    assert owner_errors == []
    assert not owner.is_alive()
    assert not watcher.is_alive()
    assert heartbeat_before_release == [True]


@pytest.mark.xfail(strict=True, reason="[Q3/F2] Wave 1 adds queue-global reservations")
async def test_queued_anchor_reservation_is_visible_to_later_settlement_admission() -> None:
    """Q3/F2: an accepted establish job must be visible before it persists."""

    async def establish(
        payload: dict[str, object],
        persisted: dict[str, object],
    ) -> None:
        if payload.get("kind") == "anchor.establish":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=7)

    observed = await _capture_legacy_admissions(
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


@pytest.mark.xfail(strict=True, reason="[Q3/F2] Wave 1 adds typed absent tombstones")
async def test_no_anchor_tombstone_is_not_upgraded_by_later_establish_admission() -> None:
    """Q3/F2: a settle accepted without an anchor keeps an absent tombstone."""

    async def establish(
        payload: dict[str, object],
        persisted: dict[str, object],
    ) -> None:
        if payload.get("kind") == "anchor.establish":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=8)

    observed = await _capture_legacy_admissions(
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


@pytest.mark.xfail(strict=True, reason="[Q3/R2-1] Wave 1 classifies card.discuss at admission")
async def test_card_discuss_reservation_is_visible_to_later_settlement_admission() -> None:
    """Q3/R2-1: card.discuss must reserve before its inline anchor mutation."""

    async def discuss(
        payload: dict[str, object],
        persisted: dict[str, object],
    ) -> None:
        if payload.get("kind") == "card.discuss":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=9)

    observed = await _capture_legacy_admissions(
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
@pytest.mark.xfail(strict=True, reason="[Q3/R2-1] Wave 1 adds exhaustive builder policy")
async def test_every_anchor_building_kind_reserves_before_enqueue(
    job_kind: str,
    producer_source: str,
    needs_anchor: bool,
) -> None:
    """Q3/R2-1: the builder policy is exhaustive at admission."""
    observed = await _capture_legacy_admissions(
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
    ("actual_ref", "actual_generation", "expected_state"),
    [
        pytest.param("hypothesis-A", 12, "persisted", id="actual-persisted"),
        pytest.param("", 0, "absent", id="actual-absent"),
    ],
)
@pytest.mark.xfail(strict=True, reason="[Q18/R2-2] Wave 1 adds failed-head advance and GC")
async def test_failed_reservation_advances_head_for_new_submit_and_gc_after_old_dependents_drain(
    actual_ref: str,
    actual_generation: int,
    expected_state: str,
) -> None:
    """Q18/R2-2: failed is old-dependency-only and retry gets a fresh owner."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    observed: dict[str, dict[str, object]] = {}
    results: dict[str, str] = {}
    effects: list[str] = []
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()
    first_old_dependency_entered = asyncio.Event()
    release_old_dependencies = asyncio.Event()

    async def handler(**payload: object) -> None:
        record = dict(payload)
        job_id = str(record.get("job_id", ""))
        observed[job_id] = record
        if job_id == "blocker":
            blocker_entered.set()
            await release_blocker.wait()
            return
        if job_id == "builder-failed":
            persisted.update(anchor_ref=actual_ref, anchor_generation=actual_generation)
            results[job_id] = "failed"
            return
        if job_id == "old-1":
            first_old_dependency_entered.set()
            await release_old_dependencies.wait()
        if job_id.startswith(("old-", "new-")):
            effects.append(job_id)
            results[job_id] = "applied"
            return
        if job_id == "retry-builder":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=13)
            results[job_id] = "persisted"

    queue = DialogueLearnQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        await queue.submit({"job_id": "blocker", "kind": "learn"})
        await asyncio.wait_for(blocker_entered.wait(), timeout=1)
        await queue.submit(
            {
                "job_id": "builder-failed",
                "kind": "anchor.establish",
                "target_ref": "hypothesis-A",
            }
        )
        for index in (1, 2):
            await queue.submit(
                {
                    "job_id": f"old-{index}",
                    "kind": "settle.hypothesis",
                    "target_ref": "hypothesis-A",
                }
            )
        release_blocker.set()
        await asyncio.wait_for(first_old_dependency_entered.wait(), timeout=1)
        await queue.submit(
            {
                "job_id": "new-after-failure",
                "kind": "settle.hypothesis",
                "target_ref": "hypothesis-A",
            }
        )
        await queue.submit(
            {
                "job_id": "retry-builder",
                "kind": "anchor.establish",
                "target_ref": "hypothesis-A",
            }
        )
        release_old_dependencies.set()
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_blocker.set()
        release_old_dependencies.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)

    new_snapshot = observed["new-after-failure"].get("anchor_snapshot")
    assert results["old-1"] == results["old-2"] == "anchor_dependency_failed"
    assert effects == []
    assert isinstance(new_snapshot, Mapping)
    assert new_snapshot.get("state") == expected_state
    assert new_snapshot.get("state") != "failed"
    assert observed["builder-failed"].get("owned_anchor_reservation_id")
    assert observed["retry-builder"].get("owned_anchor_reservation_id")
    assert (
        observed["builder-failed"]["owned_anchor_reservation_id"]
        != observed["retry-builder"]["owned_anchor_reservation_id"]
    )
    assert results["retry-builder"] == "persisted"


@pytest.mark.xfail(
    strict=True,
    reason="[Q18/Q19/R2-2/M1] Wave 1 preserves B1 persisted after B2 failed",
)
async def test_failed_reservation_after_persisted_builder_keeps_b1_as_effective_head() -> None:
    """Q18/Q19/R2-2/M1: B1 persisted then B2 failed must expose B1 to new submit."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    observed: dict[str, dict[str, object]] = {}
    results: dict[str, str] = {}
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()
    old_dependency_entered = asyncio.Event()
    release_old_dependency = asyncio.Event()

    async def handler(**payload: object) -> None:
        record = dict(payload)
        job_id = str(record.get("job_id", ""))
        observed[job_id] = record
        if job_id == "blocker":
            blocker_entered.set()
            await release_blocker.wait()
        elif job_id == "B1":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=21)
            results[job_id] = "persisted"
        elif job_id == "B2":
            results[job_id] = "failed"
        elif job_id == "S1":
            old_dependency_entered.set()
            await release_old_dependency.wait()
            results[job_id] = "applied"
        elif job_id == "S2":
            results[job_id] = "applied"

    queue = DialogueLearnQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        await queue.submit({"job_id": "blocker", "kind": "learn"})
        await asyncio.wait_for(blocker_entered.wait(), timeout=1)
        await queue.submit(
            {"job_id": "B1", "kind": "anchor.establish", "target_ref": "hypothesis-A"}
        )
        await queue.submit(
            {"job_id": "B2", "kind": "anchor.establish", "target_ref": "hypothesis-A"}
        )
        await queue.submit(
            {"job_id": "S1", "kind": "settle.hypothesis", "target_ref": "hypothesis-A"}
        )
        release_blocker.set()
        await asyncio.wait_for(old_dependency_entered.wait(), timeout=1)
        await queue.submit(
            {"job_id": "S2", "kind": "settle.hypothesis", "target_ref": "hypothesis-A"}
        )
        release_old_dependency.set()
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_blocker.set()
        release_old_dependency.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)

    b1_reservation = observed["B1"].get("owned_anchor_reservation_id")
    b2_reservation = observed["B2"].get("owned_anchor_reservation_id")
    s1_snapshot = observed["S1"].get("anchor_snapshot")
    s2_snapshot = observed["S2"].get("anchor_snapshot")
    assert b1_reservation and b2_reservation and b1_reservation != b2_reservation
    assert isinstance(s1_snapshot, Mapping)
    assert s1_snapshot.get("state") == "reserved"
    assert s1_snapshot.get("reservation_id") == b2_reservation
    assert results["S1"] == "anchor_dependency_failed"
    assert isinstance(s2_snapshot, Mapping)
    assert s2_snapshot.get("state") == "persisted"
    assert s2_snapshot.get("generation") == 21
    assert s2_snapshot.get("reservation_id") != b2_reservation


@pytest.mark.parametrize(
    "builder_kind",
    [
        pytest.param("anchor.establish", id="anchor-establish"),
        pytest.param("card.discuss", id="card-discuss"),
        pytest.param("confusion.attribution.replay", id="attribution-replay"),
    ],
)
@pytest.mark.xfail(strict=True, reason="[Q19/R3-1] Wave 1 adds owner-bound reservation entries")
async def test_same_ref_double_builder_second_noop_resolves_own_head_for_later_settlement(
    builder_kind: str,
) -> None:
    """Q19/R3-1: same-ref builders never coalesce or resolve each other."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    observed: dict[str, dict[str, object]] = {}
    outcomes: dict[str, str] = {}
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()
    s1_finished = asyncio.Event()

    async def handler(**payload: object) -> None:
        record = dict(payload)
        job_id = str(record.get("job_id", ""))
        observed[job_id] = record
        if job_id == "blocker":
            blocker_entered.set()
            await release_blocker.wait()
            return
        if job_id == "B1":
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=31)
            outcomes[job_id] = "persisted"
        elif job_id == "B2":
            outcomes[job_id] = "no_op"
        else:
            outcomes[job_id] = "applied"
            if job_id == "S1":
                s1_finished.set()

    queue = DialogueLearnQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        await queue.submit({"job_id": "blocker", "kind": "learn"})
        await asyncio.wait_for(blocker_entered.wait(), timeout=1)
        for job_id in ("B1", "B2"):
            await queue.submit(
                {
                    "job_id": job_id,
                    "kind": builder_kind,
                    "target_ref": "hypothesis-A",
                    "needs_anchor": True,
                }
            )
        await queue.submit(
            {"job_id": "S1", "kind": "settle.hypothesis", "target_ref": "hypothesis-A"}
        )
        release_blocker.set()
        await asyncio.wait_for(s1_finished.wait(), timeout=1)
        await queue.submit(
            {"job_id": "S2", "kind": "settle.hypothesis", "target_ref": "hypothesis-A"}
        )
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_blocker.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)

    b1_reservation = observed["B1"].get("owned_anchor_reservation_id")
    b2_reservation = observed["B2"].get("owned_anchor_reservation_id")
    s1_snapshot = observed["S1"].get("anchor_snapshot")
    s2_snapshot = observed["S2"].get("anchor_snapshot")
    assert b1_reservation and b2_reservation and b1_reservation != b2_reservation
    assert isinstance(s1_snapshot, Mapping)
    assert s1_snapshot.get("state") == "reserved"
    assert s1_snapshot.get("reservation_id") == b2_reservation
    assert outcomes == {"B1": "persisted", "B2": "no_op", "S1": "applied", "S2": "applied"}
    assert isinstance(s2_snapshot, Mapping)
    assert s2_snapshot.get("state") == "persisted"
    assert s2_snapshot.get("generation") == 31
    assert s2_snapshot.get("resolved_by_reservation_id") == b2_reservation


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
@pytest.mark.xfail(strict=True, reason="[Q20/R3-2] Wave 1 resolves at mutator return")
async def test_anchor_reservation_promotes_before_followup_await_throw_or_replay_short_circuit(
    terminal: str,
) -> None:
    """Q20/R3-2: mutator return synchronously resolves its exact owner."""
    persisted: dict[str, object] = {"anchor_ref": "", "anchor_generation": 0}
    observed: dict[str, dict[str, object]] = {}
    mutator_returned = asyncio.Event()
    release_followup = asyncio.Event()

    async def handler(**payload: object) -> None:
        record = dict(payload)
        job_id = str(record.get("job_id", ""))
        observed[job_id] = record
        if job_id != "builder":
            return
        if terminal in {"absent", "failed"}:
            persisted.update(anchor_ref="", anchor_generation=0)
        else:
            persisted.update(anchor_ref="hypothesis-A", anchor_generation=41)
        mutator_returned.set()
        await release_followup.wait()
        if terminal == "persisted":
            raise RuntimeError("follow-up effect failed after durable anchor mutation")

    queue = DialogueLearnQueue(handler, anchor_provider=lambda: dict(persisted))
    queue.start()
    try:
        await queue.submit(
            {
                "job_id": "builder",
                "kind": "anchor.establish",
                "target_ref": "hypothesis-A",
                "terminal": terminal,
            }
        )
        await asyncio.wait_for(mutator_returned.wait(), timeout=1)
        await queue.submit(
            {
                "job_id": "later-settle",
                "kind": "settle.hypothesis",
                "target_ref": "hypothesis-A",
            }
        )
        release_followup.set()
        await asyncio.wait_for(queue.shutdown(), timeout=2)
    finally:
        release_followup.set()
        if queue.worker_alive:
            await queue.shutdown(timeout=1)

    builder = observed["builder"]
    later_snapshot = observed["later-settle"].get("anchor_snapshot")
    assert builder.get("owned_anchor_reservation_id")
    assert builder.get("anchor_resolution_count") == 1
    assert builder.get("resolved_terminal") == terminal
    assert isinstance(later_snapshot, Mapping)
    assert later_snapshot.get("state") in {"persisted", "absent"}
    assert later_snapshot.get("state") not in {"reserved", "failed", "superseded"}
