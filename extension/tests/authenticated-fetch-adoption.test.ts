import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const protectedCallers = [
  "src/background/service-worker.ts",
  "src/background/bili-task-dispatcher.ts",
  "src/background/cookie-sync.ts",
  "src/background/debug-log.ts",
  "src/background/dy-task-dispatcher.ts",
  "src/background/e2e-runner.ts",
  "src/background/reddit-task-dispatcher.ts",
  "src/background/xhs-task-dispatcher.ts",
  "src/background/yt-task-dispatcher.ts",
  "src/background/zhihu-task-dispatcher.ts",
  "src/content/douyin.ts",
];

test("protected extension API calls adopt authenticatedFetch", () => {
  for (const relativePath of protectedCallers) {
    const source = readFileSync(resolve(relativePath), "utf8");
    const rawCalls = [...source.matchAll(/fetch\(await apiUrl\("([^\"]+)"\)/g)]
      .map((match) => match[1])
      .filter((path) => path !== "/ping" && path !== "/health");
    assert.deepEqual(rawCalls, [], `${relativePath} still has raw protected fetches`);
    if (source.includes("apiUrl(")) {
      assert.match(source, /authenticatedFetch|\/ping|\/health/);
    }
  }
});
