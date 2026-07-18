import test from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const version = JSON.parse(readFileSync("package.json", "utf8")).version;
const chromeZip = `openbiliclaw-extension-v${version}.zip`;
const firefoxZip = `openbiliclaw-extension-v${version}-firefox.zip`;

function zipLayout(path) {
  return execSync(`unzip -l "${path}"`, { encoding: "utf8" });
}

function zipEntryText(path, entry) {
  return execSync(`unzip -p "${path}" "${entry}"`, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}

test("chrome and firefox packages contain manifest, popup, bundles, and no debug relay", () => {
  // The package scripts share staging state (each build wipes the other
  // target's dist directory), so they must run sequentially inside one test
  // rather than as concurrent top-level tests, with firefox packaged first.
  execSync("npm run package:firefox", { stdio: "ignore" });
  execSync("npm run package", { stdio: "ignore" });

  const chromeLayout = zipLayout(chromeZip);
  assert.match(chromeLayout, /manifest\.json/);
  assert.match(chromeLayout, /popup\/popup\.html/);
  assert.match(chromeLayout, /popup\/popup\.js/);
  assert.match(chromeLayout, /dist\/background\/service-worker\.js/);
  for (const content of ["douyin", "bilibili", "xiaohongshu", "x", "youtube", "zhihu", "reddit"]) {
    assert.match(chromeLayout, new RegExp(`dist/content/${content}\\.js`), content);
  }
  const chromeDouyin = zipEntryText(chromeZip, "dist/content/douyin.js");
  assert.ok(!chromeDouyin.includes("/sources/_debug/log"), "debug relay leaked into chrome bundle");
  assert.ok(!/debugLog\s*\(/.test(chromeDouyin), "debugLog call leaked into chrome bundle");

  const firefoxLayout = zipLayout(firefoxZip);
  assert.match(firefoxLayout, /manifest\.json/);
  assert.match(firefoxLayout, /popup\/popup\.html/);
  assert.match(firefoxLayout, /background\/service-worker\.js/);
  assert.match(firefoxLayout, /content\/douyin\.js/);
  const firefoxDouyin = zipEntryText(firefoxZip, "content/douyin.js");
  assert.ok(!firefoxDouyin.includes("/sources/_debug/log"), "debug relay leaked into firefox bundle");
  assert.ok(!/debugLog\s*\(/.test(firefoxDouyin), "debugLog call leaked into firefox bundle");
});
