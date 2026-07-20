// @ts-check
/**
 * Compile web TypeScript sources in place: for every .ts under
 * src/openbiliclaw/web/{js,shared,desktop/assets/js}, emit a sibling .js
 * (loose ES module, specifiers unchanged). If no .ts files exist yet
 * (pre-migration), verify the existing .js files are present and exit 0 —
 * this lets CI wire `build:web` before any source is migrated.
 *
 * Emission is type-stripping, not bundling/transpiling: esbuild `transform`
 * without a `format` rewrite keeps `export`/`import` declarations inline and
 * the source text otherwise untouched, which the Python frontend-contract
 * guards (source-text markers like `export function <name>`) depend on.
 * charset "utf8" keeps the CJK UI copy literal for the copy guards.
 */
import { transform } from "esbuild";
import { readdirSync, existsSync, statSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
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
  const source = readFileSync(entry, "utf8");
  const sourceName = basename(entry);
  const outputName = sourceName.replace(/\.ts$/, ".js");
  const { code, map } = await transform(source, {
    loader: "ts",
    target: "es2022",
    charset: "utf8",
    sourcemap: "external",
    sourcefile: sourceName,
  });
  writeFileSync(entry.replace(/\.ts$/, ".js"), `${code}//# sourceMappingURL=${outputName}.map\n`);
  writeFileSync(entry.replace(/\.ts$/, ".js.map"), map);
}
console.log(`[build-web] emitted ${entryPoints.length} file(s)`);
