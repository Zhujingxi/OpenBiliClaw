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

test("execution failures leave the lease uncompleted for durable retry", async () => {
  let completionCount = 0;
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "bilibili",
    operations: ["search"],
    transport: {
      async claim() {
        return claim;
      },
      async complete(_taskId, _leaseToken, result) {
        void result;
        completionCount += 1;
      },
    },
    execute: async () => {
      throw new Error("page unavailable");
    },
  });

  await assert.rejects(() => dispatcher.pollOnce(), /page unavailable/);
  assert.equal(completionCount, 0);
});
