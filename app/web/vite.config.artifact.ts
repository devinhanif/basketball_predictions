import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// The single-file artifact build. Everything is bundled into one script and one stylesheet (fonts as data: URIs) in
// dist-artifact-tmp/, which scripts/build-artifact.mjs then inlines into dist-artifact/parlayhitters.html together with the
// baked data. transport.ts (fetch) is swapped for transport.static.ts, so no network code ships in the page.
export default defineConfig({
  oxc: { jsx: { runtime: "automatic", importSource: "preact" } },
  define: { "import.meta.env.VITE_ARTIFACT": JSON.stringify("1") },
  resolve: {
    alias: [{ find: /^\.\/transport$/, replacement: fileURLToPath(new URL("./src/transport.static.ts", import.meta.url)) }],
  },
  publicDir: false,
  build: {
    outDir: "dist-artifact-tmp",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    lib: {
      entry: fileURLToPath(new URL("./src/main.artifact.tsx", import.meta.url)),
      formats: ["iife"],
      name: "ParlayHitters",
      fileName: () => "app.js",
      cssFileName: "app",
    },
  },
});
