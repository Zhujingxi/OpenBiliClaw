// @ts-check
/**
 * Compile web TypeScript sources in place: for every .ts under
 * src/openbiliclaw/web/{js,shared,desktop/assets/js}, emit a checked-in sibling
 * .js runtime asset (loose ES module, specifiers unchanged). `--check` compares
 * those distributable assets without writing. If no .ts files exist yet,
 * there are no migrated runtime assets to verify, so the command exits 0 —
 * this lets CI wire the check before any source is migrated.
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

const args = process.argv.slice(2);
const unknownArgs = args.filter((arg) => arg !== "--check");
if (unknownArgs.length > 0) {
  console.error(`[build-web] unknown argument(s): ${unknownArgs.join(", ")}`);
  process.exit(2);
}
const checkOnly = args.includes("--check");
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

const drifted = [];
for (const entry of entryPoints) {
  const source = readFileSync(entry, "utf8");
  const sourceName = basename(entry);
  const outputName = sourceName.replace(/\.ts$/, ".js");
  const outputPath = entry.replace(/\.ts$/, ".js");
  const { code, map } = await transform(source, {
    loader: "ts",
    target: "es2022",
    charset: "utf8",
    sourcemap: "external",
    sourcefile: sourceName,
  });
  const output = `${code}//# sourceMappingURL=${outputName}.map\n`;
  if (checkOnly) {
    if (!existsSync(outputPath) || readFileSync(outputPath, "utf8") !== output) {
      drifted.push(outputPath);
    }
    continue;
  }
  writeFileSync(outputPath, output);
  writeFileSync(entry.replace(/\.ts$/, ".js.map"), map);
}
if (checkOnly && drifted.length > 0) {
  console.error("[build-web] checked-in runtime JavaScript is missing or stale:");
  for (const path of drifted) console.error(`  ${path}`);
  console.error("[build-web] regenerate with: npm run build:web");
  process.exit(1);
}
console.log(
  checkOnly
    ? `[build-web] verified ${entryPoints.length} checked-in runtime file(s)`
    : `[build-web] emitted ${entryPoints.length} file(s)`,
);
