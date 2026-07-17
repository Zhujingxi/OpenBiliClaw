import assert from "node:assert/strict";
import test from "node:test";

import {
  createSourceTaskDispatcher,
  validateClaimedTask,
  type ClaimedSourceTask,
  type SourceTaskTransport,
} from "../src/background/generic-source-task-dispatcher.ts";

const claim: ClaimedSourceTask = {
  id: "11111111-1111-4111-8111-111111111111",
  source_id: "bilibili",
  payload: { operation: "search", query: "architecture", limit: 5 },
  lease_token: "12345678901234567890",
  lease_expires_at: "2030-01-01T00:00:00Z",
  request_deadline_at: "2030-01-01T00:05:00Z",
};

test("a source dispatcher claims and completes one generic typed task", async () => {
  const calls: Array<[string, unknown]> = [];
  const transport: SourceTaskTransport = {
    async claim(sourceId) {
      calls.push(["claim", sourceId]);
      return claim;
    },
    async complete(taskId, leaseToken, result) {
      calls.push(["complete", { taskId, leaseToken, result }]);
    },
    async fail() {
      assert.fail("successful execution must not report failure");
    },
  };
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["bootstrap_import", "search"],
    transport,
    execute: async (task) => ({ operation: task.payload.operation, items: [] }),
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.deepEqual(calls, [
    ["claim", "bilibili"],
    [
      "complete",
      {
        taskId: claim.id,
        leaseToken: claim.lease_token,
        result: { operation: "search", items: [] },
      },
    ],
  ]);
});
test("a source dispatcher rejects a mismatched source before execution", () => {
  assert.throws(
    () => validateClaimedTask({ ...claim, source_id: "reddit" }, "bilibili", ["search"]),
    /source mismatch/,
  );
});

test("a source dispatcher rejects an undeclared operation before execution", () => {
  assert.throws(
    () => validateClaimedTask({ ...claim, payload: { operation: "creator", creator: "a" } }, "bilibili", ["search"]),
    /operation mismatch/,
  );
});

test("execution failures are reported without secret-bearing error text", async () => {
  const failures: unknown[] = [];
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() {
        return claim;
      },
      async complete(_taskId, _leaseToken, result) {
        void result;
        assert.fail("failed execution must not report success");
      },
      async fail(taskId, leaseToken, failure) {
        failures.push({ taskId, leaseToken, failure });
      },
    },
    execute: async () => {
      throw new Error("page unavailable with secret=do-not-send");
    },
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.deepEqual(failures, [{
    taskId: claim.id,
    leaseToken: claim.lease_token,
    failure: { code: "execution_failed", error_type: "Error" },
  }]);
  assert.equal(JSON.stringify(failures).includes("do-not-send"), false);
});

test("an ambiguous success completion is never converted into execution_failed", async () => {
  let executions = 0;
  let failures = 0;
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() { return claim; },
      async complete() { throw new TypeError("completion response lost"); },
      async fail() { failures += 1; },
    },
    execute: async () => {
      executions += 1;
      return { operation: "search", items: [{ external_id: "one" }] };
    },
  });

  await assert.rejects(() => dispatcher.pollOnce(), /completion response lost/);
  assert.equal(executions, 1);
  assert.equal(failures, 0);
});

test("a claimed source mismatch is reported through the failure completion", async () => {
  const failures: unknown[] = [];
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() {
        return { ...claim, source_id: "reddit" };
      },
      async complete() {
        assert.fail("mismatched claim must not report success");
      },
      async fail(_taskId, _leaseToken, failure) {
        failures.push(failure);
      },
    },
    execute: async () => assert.fail("mismatched claim must not execute"),
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.deepEqual(failures, [{ code: "claim_mismatch", error_type: "TaskContractError" }]);
});

test("execution stops at the request deadline and reports a typed failure", async () => {
  const failures: unknown[] = [];
  const deadlineClaim = {
    ...claim,
    request_deadline_at: new Date(Date.now() + 20).toISOString(),
  };
  const startedAt = Date.now();
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() {
        return deadlineClaim;
      },
      async complete() {
        assert.fail("deadline must not report success");
      },
      async fail(_taskId, _leaseToken, failure) {
        failures.push(failure);
      },
    },
    execute: async () => new Promise(() => undefined),
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.ok(Date.now() - startedAt < 250, "dispatcher must not wait past the request deadline");
  assert.deepEqual(failures, [{ code: "deadline_exceeded", error_type: "TaskDeadlineError" }]);
});

test("an expired claim is failed without invoking its executor", async () => {
  const failures: unknown[] = [];
  let executions = 0;
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() {
        return { ...claim, request_deadline_at: new Date(Date.now() - 1_000).toISOString() };
      },
      async complete() {
        assert.fail("expired claim must not report success");
      },
      async fail(_taskId, _leaseToken, failure) {
        failures.push(failure);
      },
    },
    execute: async () => {
      executions += 1;
      return { operation: "search", items: [] };
    },
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.equal(executions, 0);
  assert.deepEqual(failures, [{ code: "deadline_exceeded", error_type: "TaskDeadlineError" }]);
});

test("deadline aborts in-flight execution and ignores a late successful result", async () => {
  const failures: unknown[] = [];
  const completions: unknown[] = [];
  let receivedSignal: AbortSignal | undefined;
  let resolveExecution: ((value: { operation: "search"; items: [] }) => void) | undefined;
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() {
        return {
          ...claim,
          request_deadline_at: new Date(Date.now() + 2_020).toISOString(),
        };
      },
      async complete(_taskId, _leaseToken, result) {
        completions.push(result);
      },
      async fail(_taskId, _leaseToken, failure) {
        failures.push(failure);
      },
    },
    execute: async (_task, signal) => {
      receivedSignal = signal;
      return new Promise((resolve) => {
        resolveExecution = resolve;
      });
    },
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.equal(receivedSignal?.aborted, true);
  assert.deepEqual(failures, [{ code: "deadline_exceeded", error_type: "TaskDeadlineError" }]);
  resolveExecution?.({ operation: "search", items: [] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(completions, []);
});
