import assert from "node:assert/strict";
import test from "node:test";

import { dispatcherMutexHolder } from "../src/background/dispatcher-mutex.ts";
import {
  handleNativeSaveContentResult,
  runNativeSaveTask,
} from "../src/background/native-save-task-runner.ts";
import type { NativeSaveResult, NativeSaveTask } from "../src/shared/native-save.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

const task: NativeSaveTask = {
  id: "123e4567-e89b-12d3-a456-426614174000",
  type: "native_save",
  platform: "reddit",
  platform_slug: "reddit",
  item_key: "reddit:t3_abc",
  content_id: "t3_abc",
  content_url: "https://www.reddit.com/r/test/comments/abc/demo/",
  content_type: "post",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "Reddit Saved",
};

function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

test("native save runner opens an active allow-listed URL and posts one correlated result", async () => {
  const state = installChromeMock();
  const posted: NativeSaveResult[] = [];
  state.sendMessageImpl = async () => ({ ready: true });
  try {
    const running = runNativeSaveTask(task, "reddit", async (result) => { posted.push(result); }, { timeoutMs: 100 });
    await tick();
    assert.deepEqual(state.createdTabs, [{ active: true, url: task.content_url }]);
    state.emitRuntimeMessage({ type: "NATIVE_SAVE_RESULT", platform: "reddit", task_id: task.id, item_key: task.item_key, status: "synced" }, { tab: { id: 42, url: task.content_url } });
    state.emitRuntimeMessage({ type: "NATIVE_SAVE_RESULT", platform: "reddit", task_id: task.id, item_key: task.item_key, status: "synced" }, { tab: { id: 42, url: task.content_url } });
    await running;
    assert.deepEqual(posted, [{ task_id: task.id, item_key: task.item_key, status: "synced", error_code: "", error_message: "" }]);
    assert.deepEqual(state.removedTabs, [42]);
    assert.equal(dispatcherMutexHolder(), null);
  } finally {
    state.restore();
  }
});

test("native save runner retries readiness but never retries the mutation", async () => {
  const state = installChromeMock();
  let attempts = 0;
  state.sendMessageImpl = async () => {
    attempts += 1;
    if (attempts < 3) throw new Error("Receiving end does not exist");
    return { ready: true };
  };
  try {
    const running = runNativeSaveTask(task, "reddit", async () => {}, { timeoutMs: 150, readinessRetryMs: 1 });
    await tick();
    await tick();
    await tick();
    assert.equal(state.sentMessages.length, 3);
    state.emitRuntimeMessage({ type: "NATIVE_SAVE_RESULT", platform: "reddit", task_id: task.id, item_key: task.item_key, status: "already_synced" }, { tab: { id: 42, url: task.content_url } });
    await running;
    assert.equal(state.sentMessages.length, 3);
  } finally {
    state.restore();
  }
});

test("native save runner ignores mismatched tab, platform, task ID, and item key", async () => {
  const state = installChromeMock();
  const posted: NativeSaveResult[] = [];
  try {
    const running = runNativeSaveTask(task, "reddit", async (result) => { posted.push(result); }, { timeoutMs: 100 });
    await tick();
    const base = { type: "NATIVE_SAVE_RESULT", platform: "reddit", task_id: task.id, item_key: task.item_key, status: "synced" };
    state.emitRuntimeMessage(base, { tab: { id: 99, url: task.content_url } });
    state.emitRuntimeMessage({ ...base, platform: "twitter" }, { tab: { id: 42, url: task.content_url } });
    state.emitRuntimeMessage({ ...base, task_id: "wrong" }, { tab: { id: 42, url: task.content_url } });
    state.emitRuntimeMessage({ ...base, item_key: "reddit:t3_wrong" }, { tab: { id: 42, url: task.content_url } });
    assert.equal(handleNativeSaveContentResult(base), false);
    assert.equal(posted.length, 0);
    assert.equal(
      handleNativeSaveContentResult(base, { tab: { id: 42, url: task.content_url } } as chrome.runtime.MessageSender),
      true,
    );
    await running;
  } finally {
    state.restore();
  }
});

test("native save timeout posts the fixed safe failure and releases all resources", async () => {
  const state = installChromeMock();
  const posted: NativeSaveResult[] = [];
  try {
    await runNativeSaveTask(task, "reddit", async (result) => { posted.push(result); }, { timeoutMs: 5, readinessRetryMs: 1 });
    assert.deepEqual(posted, [{ task_id: task.id, item_key: task.item_key, status: "failed", error_code: "native_save_timeout", error_message: "Platform native-save task timed out" }]);
    assert.deepEqual(state.removedTabs, [42]);
    assert.equal(dispatcherMutexHolder(), null);
    assert.equal(state.sentMessages.length, 1);
  } finally {
    state.restore();
  }
});

test("native save runner declines mismatched slug or a busy shared mutex", async () => {
  const state = installChromeMock();
  try {
    await assert.rejects(runNativeSaveTask(task, "x", async () => {}), /platform slug/);
    const { tryAcquireDispatcherMutex, releaseDispatcherMutex } = await import("../src/background/dispatcher-mutex.ts");
    assert.equal(tryAcquireDispatcherMutex("other"), true);
    await runNativeSaveTask(task, "reddit", async () => {});
    assert.deepEqual(state.createdTabs, []);
    releaseDispatcherMutex("other");
  } finally {
    state.restore();
  }
});
