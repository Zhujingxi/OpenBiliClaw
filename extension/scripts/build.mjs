import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");

const entrypoints = [
  {
    entry: resolve(root, "src/background/service-worker.ts"),
    outfile: resolve(root, "dist/background/service-worker.js"),
  },
  {
    entry: resolve(root, "src/content/bilibili.ts"),
    outfile: resolve(root, "dist/content/bilibili.js"),
  },
  {
    entry: resolve(root, "src/content/xiaohongshu.ts"),
    outfile: resolve(root, "dist/content/xiaohongshu.js"),
  },
  {
    entry: resolve(root, "src/main/xhs-token-sniffer.ts"),
    outfile: resolve(root, "dist/main/xhs-token-sniffer.js"),
  },
  {
    entry: resolve(root, "src/main/xhs-state-bridge.ts"),
    outfile: resolve(root, "dist/main/xhs-state-bridge.js"),
  },
  {
    entry: resolve(root, "src/main/dy-fetch-tap.ts"),
    outfile: resolve(root, "dist/main/dy-fetch-tap.js"),
  },
];

for (const target of entrypoints) {
  await mkdir(dirname(target.outfile), { recursive: true });
  await build({
    entryPoints: [target.entry],
    outfile: target.outfile,
    bundle: true,
    format: "iife",
    platform: "browser",
    target: "chrome120",
    sourcemap: true,
    logLevel: "info",
  });
}
