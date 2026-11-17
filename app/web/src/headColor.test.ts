// The browser bar colours: index.html declares one per system theme, and prefs.ts repeats them for a theme picked in the
// app. The two must not drift apart.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { HEAD_COLOR } from "./prefs";

describe("browser bar colour", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const declared = (scheme: "light" | "dark"): string | undefined =>
    new RegExp(`<meta name="theme-color" content="(#[0-9a-f]{6})" media="\\(prefers-color-scheme: ${scheme}\\)"`).exec(html)?.[1];

  it("is the same in index.html and in prefs for light and dark", () => {
    expect(declared("light")).toBe(HEAD_COLOR.light);
    expect(declared("dark")).toBe(HEAD_COLOR.dark);
  });
});
