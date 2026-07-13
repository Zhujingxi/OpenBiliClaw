import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { handleXTaskAlarm, isValidXTask, startXTaskPolling } from "../src/background/x-task-dispatcher.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const task: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174004",
  type: "native_save",
  platform: "twitter",
  platform_slug: "x",
  item_key: "twitter:1234567890",
  content_id: "1234567890",
  content_url: "https://x.com/i/status/1234567890",
  content_type: "tweet",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "X Bookmarks",
};

test("x task accepts only the exact native_save platform contract", () => {
  assert.equal(isValidXTask(task), true);
  assert.equal(isValidXTask({ ...task, platform_slug: "reddit" }), false);
  assert.equal(isValidXTask({ ...task, content_id: "alice", item_key: "twitter:alice" }), true);
});

test("x task polling registers and handles the exact alarm", () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const alarms: Array<{ name: string; periodInMinutes: number }> = [];
  (globalThis as { chrome?: unknown }).chrome = {
    alarms: { create: (name: string, info: { periodInMinutes: number }) => alarms.push({ name, ...info }) },
  };
  try {
    startXTaskPolling();
    handleXTaskAlarm("not-x");
    assert.deepEqual(alarms, [{ name: "openbiliclaw-x-task-poll", periodInMinutes: 1 }]);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("x task dispatcher uses authenticated exact endpoints and the shared runner", () => {
  const source = readFileSync(resolve("src/background/x-task-dispatcher.ts"), "utf8");
  assert.match(source, /authenticatedFetch\(await apiUrl\("\/sources\/x\/next-task"\)/);
  assert.match(source, /authenticatedFetch\(await apiUrl\("\/sources\/x\/task-result"\)/);
  assert.match(source, /runNativeSaveTask\(task, "x", postTaskResult\)/);
  assert.doesNotMatch(source, /queryId|GraphQL/);
});
