// @ts-check
/**
 * Export-map snapshot tool (static — does not import the module, so it works
 * for browser modules that touch document/window at module scope). Extracts
 * the sorted set of exported binding names from `export` declarations:
 *   export function f / export async function f / export class C
 *   export const|let|var x / export { a, b as c } / export default
 * Multi-line `export { ... }` lists are supported.
 *
 * Usage:
 *   node scripts/snapshot-exports.mjs write <module-path> <snapshot-file>
 *   node scripts/snapshot-exports.mjs check <module-path> <snapshot-file>
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

const [mode, modulePath, snapshotFile] = process.argv.slice(2);
if (!mode || !modulePath || !snapshotFile || !["write", "check"].includes(mode)) {
  console.error("usage: node scripts/snapshot-exports.mjs <write|check> <module-path> <snapshot-file>");
  process.exit(2);
}

const absModule = isAbsolute(modulePath) ? modulePath : join(repoRoot, modulePath);
const absSnapshot = isAbsolute(snapshotFile) ? snapshotFile : join(repoRoot, snapshotFile);

/** @param {string} src @returns {string[]} */
export function extractExports(src) {
  // Strip block comments and line comments to avoid false positives.
  const stripped = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  /** @type {Set<string>} */
  const names = new Set();
  // export function/class/const/let/var/async function
  const declRe =
    /export\s+(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)/g;
  for (const m of stripped.matchAll(declRe)) names.add(m[1]);
  // export { a, b as c, ... } (possibly multiline), not "export { } from" re-exports' origins
  const listRe = /export\s*(?:type\s+)?\{([^}]*)\}/g;
  for (const m of stripped.matchAll(listRe)) {
    for (const part of m[1].split(",")) {
      const p = part.trim();
      if (!p) continue;
      const asMatch = p.match(/(?:^|\s)as\s+([A-Za-z_$][\w$]*)$/);
      if (asMatch) {
        names.add(asMatch[1]);
      } else {
        const plain = p.match(/^([A-Za-z_$][\w$]*)$/);
        if (plain) names.add(plain[1]);
      }
    }
  }
  if (/export\s+default/.test(stripped)) names.add("default");
  return [...names].sort();
}

const src = readFileSync(absModule, "utf8");
const exports_ = extractExports(src);

if (mode === "write") {
  mkdirSync(dirname(absSnapshot), { recursive: true });
  writeFileSync(absSnapshot, JSON.stringify(exports_, null, 2) + "\n");
  console.log(`[snapshot-exports] wrote ${exports_.length} exports -> ${absSnapshot}`);
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(absSnapshot, "utf8"));
const missing = baseline.filter((/** @type {string} */ k) => !exports_.includes(k));
const added = exports_.filter((k) => !baseline.includes(k));

if (missing.length === 0 && added.length === 0) {
  console.log(`[snapshot-exports] OK: ${exports_.length} exports match ${absSnapshot}`);
  process.exit(0);
}
console.error(`[snapshot-exports] MISMATCH against ${absSnapshot}`);
if (missing.length) console.error(`  missing: ${missing.join(", ")}`);
if (added.length) console.error(`  added:   ${added.join(", ")}`);
process.exit(1);
