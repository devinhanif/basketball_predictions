// The artifact page must carry the same stylesheets as the regular build: a missing one broke the Track record layout
// at phone width (its rules live in modes.css).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = (f: string): string[] => [...readFileSync(join(__dirname, f), "utf8").matchAll(/^import "\.\/([\w-]+\.css)";$/gm)].map((m) => m[1] ?? "");

describe("stylesheets", () => {
  it("main.artifact.tsx imports exactly the stylesheets main.tsx does, in the same order", () => {
    expect(css("main.tsx").length).toBeGreaterThan(5);
    expect(css("main.artifact.tsx")).toEqual(css("main.tsx"));
  });
});
