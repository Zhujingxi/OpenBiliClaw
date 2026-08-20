import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
  build: {
    emptyOutDir: true,
    rollupOptions: {
      input: {
        popup: resolve(__dirname, "src/popup/index.html"),
      },
      output: {
        entryFileNames: "[name]/[name].js",
        chunkFileNames: "shared/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
