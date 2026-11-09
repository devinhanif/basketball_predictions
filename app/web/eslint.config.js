import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "dist-artifact", "dist-artifact-tmp", "artifact-data", "node_modules", "public", "scripts/audit-dom.js", "scripts/fake-claude.js"] },
  {
    files: ["src/**/*.{ts,tsx}", "vite.config.ts", "vite.config.artifact.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
    },
  },
  {
    files: ["eslint.config.js", "scripts/*.mjs"],
    extends: [js.configs.recommended],
    languageOptions: { globals: { URL: "readonly", console: "readonly", process: "readonly", Buffer: "readonly", fetch: "readonly", WebSocket: "readonly", setTimeout: "readonly" } },
  },
);
