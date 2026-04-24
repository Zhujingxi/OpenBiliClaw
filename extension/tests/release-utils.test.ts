import test from "node:test";
import assert from "node:assert/strict";

import {
  makeExtensionArchiveName,
  normalizeReleaseVersion,
} from "../scripts/release-utils.mjs";

test("normalizeReleaseVersion strips extension channel prefix", () => {
  assert.equal(normalizeReleaseVersion("extension-v0.1.3"), "v0.1.3");
});

test("normalizeReleaseVersion preserves plain manifest versions", () => {
  assert.equal(normalizeReleaseVersion("0.1.3"), "v0.1.3");
});

test("makeExtensionArchiveName keeps only the user-facing version", () => {
  assert.equal(
    makeExtensionArchiveName("extension-v0.1.3"),
    "openbiliclaw-extension-v0.1.3.zip",
  );
});
