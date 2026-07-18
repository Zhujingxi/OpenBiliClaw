import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const DIST_FILES = [
  "dist/background/service-worker.js",
  "dist/content/bilibili.js",
  "dist/content/douyin.js",
  "dist/content/xiaohongshu.js",
];

const CONTENT_SCRIPT_FILES = [
  "dist/content/bilibili.js",
  "dist/content/douyin.js",
  "dist/content/xiaohongshu.js",
];

test("built extension runtime scripts are directly loadable by Chrome", () => {
  const root = process.cwd();
  execFileSync("npm", ["run", "build"], { cwd: root, stdio: "pipe" });

  for (const relativePath of DIST_FILES) {
    const content = readFileSync(join(root, relativePath), "utf8");
    const matches = content.matchAll(/from\s+["'](\.\.?\/[^"']+)["']/g);
    for (const match of matches) {
      const specifier = match[1];
      assert.ok(specifier?.endsWith(".js"), `missing .js extension in ${relativePath}: ${specifier}`);
    }
  }

  for (const relativePath of CONTENT_SCRIPT_FILES) {
    const content = readFileSync(join(root, relativePath), "utf8");
    assert.doesNotMatch(
      content,
      /^\s*import\s/m,
      `content script ${relativePath} must not contain ESM imports`,
    );
  }

  // Given: a fresh build of every JavaScript runtime bundle.
  const distRoot = join(root, "dist");
  const bundleFiles = readdirSync(distRoot, { encoding: "utf8", recursive: true })
    .filter((relativePath) => relativePath.endsWith(".js"));
  const forbiddenEndpoint = ["/sources/_debug", "/log"].join("");
  const forbiddenCall = new RegExp("\\bdebug" + "Log\\s*\\(");

  // When: the generated bundles are scanned for the temporary relay.
  const bundles = bundleFiles.map((relativePath) => ({
    relativePath,
    content: readFileSync(join(distRoot, relativePath), "utf8"),
  }));
  const actual = {
    endpointReferences: bundles
      .filter(({ content }) => content.includes(forbiddenEndpoint))
      .map(({ relativePath }) => relativePath),
    helperCalls: bundles
      .filter(({ content }) => forbiddenCall.test(content))
      .map(({ relativePath }) => relativePath),
  };

  // Then: no distributable code can reach or call the removed relay.
  assert.deepEqual(actual, { endpointReferences: [], helperCalls: [] });
});
