from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path("src/openbiliclaw/web/desktop/assets/js/pending-actions.js")


def test_pending_action_coordinator_commit_undo_failure_and_flush() -> None:
    node = f"""
const assert = require("node:assert/strict");
const {{ createPendingActionCoordinator }} = require({json.dumps(str(SCRIPT.resolve()))});

(async () => {{
  const timers = new Map();
  let nextTimer = 1;
  const setTimer = (fn) => {{
    const id = nextTimer++;
    timers.set(id, fn);
    return id;
  }};
  const clearTimer = (id) => timers.delete(id);
  const fire = async (id) => {{
    const fn = timers.get(id);
    timers.delete(id);
    fn();
    await new Promise((resolve) => setImmediate(resolve));
  }};
  const commits = [];
  const rollbacks = [];
  const committed = [];
  const errors = [];
  const coordinator = createPendingActionCoordinator({{
    windowMs: 10000,
    setTimer,
    clearTimer,
    onCommitError: (error, key) => errors.push([key, error.message]),
  }});

  assert.equal(coordinator.schedule("a", {{
    commit: (options) => commits.push(["a", options.keepalive]),
    rollback: (details) => rollbacks.push(["a", details.reason]),
    committed: () => committed.push("a"),
  }}), true);
  assert.equal(coordinator.schedule("a", {{
    commit() {{ throw new Error("duplicate"); }},
    rollback() {{}},
  }}), false);
  assert.equal(commits.length, 0);
  assert.equal(coordinator.undo("a"), true);
  assert.deepEqual(rollbacks, [["a", "undo"]]);
  assert.equal(commits.length, 0);

  assert.equal(coordinator.schedule("b", {{
    commit: (options) => commits.push(["b", options.keepalive]),
    rollback() {{}},
    committed: () => committed.push("b"),
  }}), true);
  await fire(coordinator.get("b").timerId);
  assert.deepEqual(commits, [["b", false]]);
  assert.deepEqual(committed, ["b"]);

  assert.equal(coordinator.schedule("c", {{
    commit: () => Promise.reject(new Error("boom")),
    rollback: (details) => rollbacks.push(["c", details.reason]),
  }}), true);
  await fire(coordinator.get("c").timerId);
  assert.deepEqual(rollbacks.at(-1), ["c", "error"]);
  assert.deepEqual(errors, [["c", "boom"]]);

  assert.equal(coordinator.schedule("d", {{
    commit: (options) => commits.push(["d", options.keepalive]),
    rollback() {{}},
  }}), true);
  const timerId = coordinator.get("d").timerId;
  const staleTimerCallback = timers.get(timerId);
  const flushPromise = coordinator.flushAll();
  assert.deepEqual(commits.at(-1), ["d", true]);
  staleTimerCallback();
  await new Promise((resolve) => setImmediate(resolve));
  await flushPromise;
  assert.deepEqual(commits.at(-1), ["d", true]);
  assert.equal(commits.filter(([key]) => key === "d").length, 1);
  assert.equal(coordinator.undo("d"), false);
}})().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    subprocess.run(["node", "--input-type=commonjs", "-e", node], check=True)
