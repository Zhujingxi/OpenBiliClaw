import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const DIST_FILES = [
  "dist/background/service-worker.js",
  "dist/content/bilibili.js",
  "dist/content/xiaohongshu.js",
];

const CONTENT_SCRIPT_FILES = ["dist/content/bilibili.js", "dist/content/xiaohongshu.js"];

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
});
