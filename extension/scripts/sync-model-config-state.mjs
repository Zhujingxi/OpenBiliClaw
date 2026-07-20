#!/usr/bin/env node
/**
 * Sync the canonical model-config state module into the extension popup.
 *
 * The popup is shipped as loose ES modules (popup/ is zipped verbatim — see
 * scripts/package.mjs), so it cannot import from src/openbiliclaw/web/shared/
 * at runtime. Per the setup/configuration redesign plan (decision 11), the
 * 907-line fork is deleted and replaced by a checked-in generated copy of the
 * shared module; tests/js/model-config-parity.test.mjs asserts the copy never
 * drifts from the source.
 *
 * Usage: node extension/scripts/sync-model-config-state.mjs [--check]
 *   --check   exit non-zero when the popup copy differs from the source
 *             (used by CI-style drift guards) without rewriting the file.
 */
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..", "..");
const sourcePath = resolve(root, "src/openbiliclaw/web/shared/model-config-state.js");
const targetPath = resolve(root, "extension/popup/popup-model-config-state.js");

const BANNER = `// GENERATED FILE — DO NOT EDIT DIRECTLY.
// Source of truth: model-config-state.js in the web app's shared modules
// (see src/openbiliclaw/web for the canonical copy).
// Regenerate with: node extension/scripts/sync-model-config-state.mjs
// Drift guard: tests/js/model-config-parity.test.mjs

`;

const source = await readFile(sourcePath, "utf-8");
const generated = BANNER + source;

const checkOnly = process.argv.includes("--check");
let current = null;
try {
  current = await readFile(targetPath, "utf-8");
} catch {
  current = null;
}

if (current === generated) {
  console.log("popup-model-config-state.js is up to date.");
  process.exit(0);
}

if (checkOnly) {
  console.error(
    "popup-model-config-state.js is out of sync with src/openbiliclaw/web/shared/model-config-state.js.\n" +
      "Run: node extension/scripts/sync-model-config-state.mjs",
  );
  process.exit(1);
}

await writeFile(targetPath, generated);
console.log(`Wrote ${targetPath} (${generated.length} bytes).`);
