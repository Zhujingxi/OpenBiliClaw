// @ts-check
/**
 * Compile web TypeScript sources in place: for every .ts under
 * src/openbiliclaw/web/{js,shared,desktop/assets/js}, emit a sibling .js
 * (loose ES module, specifiers unchanged). If no .ts files exist yet
 * (pre-migration), verify the existing .js files are present and exit 0 —
 * this lets CI wire `build:web` before any source is migrated.
 */
import { build } from "esbuild";
import { readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const webRoot = join(repoRoot, "src", "openbiliclaw", "web");
const roots = [
  join(webRoot, "js"),
  join(webRoot, "shared"),
  join(webRoot, "desktop", "assets", "js"),
];

/** @param {string} dir @returns {string[]} */
function walkTs(dir) {
  /** @type {string[]} */
  const out = [];
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) {
      out.push(...walkTs(p));
    } else if (entry.endsWith(".ts") && !entry.endsWith(".d.ts")) {
      out.push(p);
    }
  }
  return out;
}

const entryPoints = roots.flatMap(walkTs);

if (entryPoints.length === 0) {
  console.log("[build-web] no .ts sources yet; nothing to emit (pre-migration layout OK)");
  process.exit(0);
}

for (const entry of entryPoints) {
  await build({
    entryPoints: [entry],
    outfile: entry.replace(/\.ts$/, ".js"),
    bundle: false,
    format: "esm",
    target: "es2022",
    platform: "browser",
    sourcemap: true,
    logLevel: "warning",
  });
}
console.log(`[build-web] emitted ${entryPoints.length} file(s)`);
