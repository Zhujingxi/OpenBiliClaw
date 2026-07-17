import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { BROWSER_SOURCE_OPERATIONS } from "../src/background/browser-source-executor.ts";

test("generic dispatcher retains the declared seven-source browser capability matrix", () => {
  assert.deepEqual(BROWSER_SOURCE_OPERATIONS, {
    bilibili: ["search"],
    xiaohongshu: ["bootstrap_import", "search", "creator"],
    douyin: ["bootstrap_import", "search", "trending", "feed"],
    youtube: ["bootstrap_import"],
    zhihu: ["bootstrap_import", "search", "trending", "feed", "creator", "related"],
    reddit: ["bootstrap_import", "search", "trending", "community", "related"],
  });
  assert.equal(BROWSER_SOURCE_OPERATIONS.twitter, undefined);
});

test("every retained executor and Twitter passive collector remains in the build graph", () => {
  const contentContracts = {
    bilibili: "installBiliMessageListener",
    xiaohongshu: "registerTaskExecutor",
    douyin: "registerDyScopeExecutor",
    youtube: "installYtMessageListener",
    zhihu: "installZhihuMessageListener",
    reddit: "installRedditMessageListener",
    x: "startCollector(twitterAdapter)",
  };
  const manifest = readFileSync(resolve("manifest.json"), "utf8");
  const build = readFileSync(resolve("scripts/build.mjs"), "utf8");
  const worker = readFileSync(resolve("src/background/service-worker.ts"), "utf8");
  assert.match(worker, /createSourceTaskDispatcher/);
  assert.match(worker, /executeBrowserSourceTask/);
  for (const [source, marker] of Object.entries(contentContracts)) {
    const sourceText = readFileSync(resolve(`src/content/${source}.ts`), "utf8");
    assert.match(sourceText, new RegExp(marker.replace(/[()]/g, "\\$&")));
    assert.match(manifest, new RegExp(`dist/content/${source}\\.js`));
    assert.match(build, new RegExp(`src/content/${source}\\.ts`));
  }
});

test("Xiaohongshu keeps page-derived xsec tokens without the removed network sniffer", () => {
  const entry = readFileSync(resolve("src/content/xiaohongshu.ts"), "utf8");
  const bootstrap = readFileSync(resolve("src/content/xhs/bootstrap.ts"), "utf8");
  assert.match(entry, /XHS_URLS_OBSERVED/);
  assert.match(entry, /xsec_token/);
  assert.match(bootstrap, /noteCard.*xsec_token/s);
  assert.equal(readFileSync(resolve("scripts/build.mjs"), "utf8").includes("xhs-token-sniffer"), false);
});
