import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const protectedCallers = [
  "src/background/service-worker.ts",
  "src/background/bili-task-dispatcher.ts",
  "src/background/cookie-sync.ts",
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

test("production sources omit the temporary daemon relay", () => {
  // Given: every TypeScript production source in the extension.
  const sourceRoot = resolve("src");
  const sourceFiles = readdirSync(sourceRoot, { encoding: "utf8", recursive: true })
    .filter((relativePath) => relativePath.endsWith(".ts"));
  const forbiddenEndpoint = ["/sources/_debug", "/log"].join("");
  const forbiddenCall = new RegExp("\\bdebug" + "Log\\s*\\(");
  const relayModulePath = resolve(["src/background/debug", "-log.ts"].join(""));

  // When: source contents are checked for the relay endpoint and helper calls.
  const sources = sourceFiles.map((relativePath) => ({
    relativePath,
    content: readFileSync(resolve(sourceRoot, relativePath), "utf8"),
  }));
  const actual = {
    endpointReferences: sources
      .filter(({ content }) => content.includes(forbiddenEndpoint))
      .map(({ relativePath }) => relativePath),
    helperCalls: sources
      .filter(({ content }) => forbiddenCall.test(content))
      .map(({ relativePath }) => relativePath),
    relayModuleExists: existsSync(relayModulePath),
  };

  // Then: neither the relay implementation nor any production call site remains.
  assert.deepEqual(actual, {
    endpointReferences: [],
    helperCalls: [],
    relayModuleExists: false,
  });
});
