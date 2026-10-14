import { defineConfig } from "vitest/config";

// The browser only ever talks to its own origin. /api is proxied to the read-only API bound to
// 127.0.0.1, so a phone on the same Wi-Fi reaches the app through this server and the API itself
// is never exposed to the network (and needs no wide CORS rule).
// API_ORIGIN overrides the target when port 8000 is taken (it must still be a 127.0.0.1 address).
const api = { "/api": { target: process.env["API_ORIGIN"] ?? "http://127.0.0.1:8000", changeOrigin: false } };

export default defineConfig({
  oxc: { jsx: { runtime: "automatic", importSource: "preact" } },
  server: { port: 5173, strictPort: true, proxy: api },
  preview: { port: 4173, strictPort: true, proxy: api },
  build: { sourcemap: false, target: "es2022" },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
