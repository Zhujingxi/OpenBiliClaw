// @ts-check
/**
 * Compile extension popup TypeScript sources: popup/*.ts -> popup-built/*.js
 * (loose ES modules, .js specifiers preserved — popup.html loads them
 * verbatim; package.mjs copies popup-built/ over the zipped popup/).
 * Pre-migration (no popup/*.ts yet) this is a no-op so the build keeps
 * working on the un-migrated tree.
 */
import { build } from "esbuild";
import { readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const extRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const popupDir = join(extRoot, "popup");
const outDir = join(extRoot, "popup-built");

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

const entryPoints = walkTs(popupDir);

if (entryPoints.length === 0) {
  console.log("[build-popup] no popup/*.ts sources yet; nothing to emit (pre-migration layout OK)");
  process.exit(0);
}

for (const entry of entryPoints) {
  const rel = entry.slice(popupDir.length + 1).replace(/\.ts$/, ".js");
  await build({
    entryPoints: [entry],
    outfile: join(outDir, rel),
    bundle: false,
    format: "esm",
    target: "es2022",
    platform: "browser",
    sourcemap: true,
    logLevel: "warning",
  });
}
console.log(`[build-popup] emitted ${entryPoints.length} file(s) -> popup-built/`);
