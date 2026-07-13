import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSaveTask,
} from "../src/shared/native-save.ts";

const TASK_ID = "123e4567-e89b-12d3-a456-426614174000";
const validTask: NativeSaveTask = {
  id: TASK_ID,
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

test("native save accepts only the exact platform, slug, and HTTPS host contract", () => {
  const cases = [
    ["youtube", "yt", "https://youtu.be/video-123"],
    ["xiaohongshu", "xhs", "https://www.xiaohongshu.com/explore/note-123"],
    ["douyin", "dy", "https://www.iesdouyin.com/share/video/123"],
    ["twitter", "x", "https://twitter.com/user/status/123"],
    ["zhihu", "zhihu", "https://www.zhihu.com/question/1/answer/2"],
    ["reddit", "reddit", "https://redd.it/abc"],
  ] as const;

  for (const [platform, platform_slug, content_url] of cases) {
    assert.equal(isNativeSaveTask({
      ...validTask,
      platform,
      platform_slug,
      item_key: `${platform}:content-123`,
      content_id: "content-123",
      content_url,
    }), true);
  }
  assert.equal(isNativeSaveTask({ ...validTask, type: "search" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, platform_slug: "x" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "javascript:alert(1)" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "http://reddit.com/post" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://reddit.com.evil.test/post" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://user:secret@reddit.com/post" }), false);
});

test("native save rejects malformed, overlong, and inconsistent task fields", () => {
  assert.equal(isNativeSaveTask(validTask), true);
  assert.equal(isNativeSaveTask({ ...validTask, id: "not-a-canonical-uuid" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, item_key: "reddit:t3_wrong" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, item_key: "reddit:bad id", content_id: "bad id" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_id: "x".repeat(513) }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_type: "x".repeat(129) }), false);
  assert.equal(isNativeSaveTask({ ...validTask, requested_action: "like" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, target_label: "x".repeat(257) }), false);
  assert.equal(isNativeSaveTask({ ...validTask, cookie: "secret" }), false);
});

test("native save result sanitizer emits only backend allow-listed status and code pairs", () => {
  assert.deepEqual(sanitizeNativeSaveResult({ status: "login_required", response_body: "<html>secret" }), {
    status: "login_required",
    error_code: "",
    error_message: "Platform login required",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "unsupported", error_code: "unsupported_content_type" }), {
    status: "unsupported",
    error_code: "unsupported_content_type",
    error_message: "Content type is unsupported for platform native save",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "unsupported", error_message: "raw response" }), {
    status: "unsupported",
    error_code: "unsupported_content_type",
    error_message: "Content type is unsupported for platform native save",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "rate_limited", error_message: "token=secret" }), {
    status: "rate_limited",
    error_code: "",
    error_message: "Platform native save rate limited",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "failed", error_code: "unknown", error_message: "<body>cookie=secret</body>" }), {
    status: "failed",
    error_code: "native_save_failed",
    error_message: "Platform native save failed",
  });
});

test("native save content runtime allows the matching hostname and executes once per task ID", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalLocation = (globalThis as { location?: unknown }).location;
  const listeners: Array<(message: unknown) => unknown> = [];
  const emitted: unknown[] = [];
  (globalThis as { location?: unknown }).location = { hostname: "www.reddit.com" };
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: { addListener: (listener: (message: unknown) => unknown) => listeners.push(listener) },
      sendMessage: async (message: unknown) => emitted.push(message),
    },
  };
  try {
    const { installNativeSaveExecutor } = await import(`../src/content/native-save/runtime.ts?test=${Date.now()}`);
    let executions = 0;
    installNativeSaveExecutor("reddit", async () => {
      executions += 1;
      return { status: "synced" };
    });
    const execute = { type: "NATIVE_SAVE_EXECUTE", task: validTask };
    assert.equal(await listeners[0](execute), true);
    assert.equal(await listeners[0](execute), true);
    assert.equal(executions, 1);
    assert.deepEqual(emitted, [{
      type: "NATIVE_SAVE_RESULT",
      platform: "reddit",
      task_id: TASK_ID,
      item_key: validTask.item_key,
      status: "synced",
      error_code: "",
      error_message: "",
    }]);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    (globalThis as { location?: unknown }).location = originalLocation;
  }
});

test("native save shared contract is documented as infrastructure without a wired executor", () => {
  const runtime = readFileSync(resolve("../docs/modules/runtime.md"), "utf8");
  const changelog = readFileSync(resolve("../docs/changelog.md"), "utf8");
  const architecture = readFileSync(resolve("../docs/architecture.md"), "utf8");
  for (const text of [runtime, changelog, architecture]) {
    assert.match(text, /NATIVE_SAVE_EXECUTE/);
    assert.match(text, /尚未接入平台 executor/);
  }
});
