import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { hashForRoute, parse, topHref } from "../router";
import { SCREEN_ITEMS, screensFor } from "../shellData";

const SRC = join(import.meta.dirname, "..");
const read = (f: string): string => readFileSync(join(SRC, f), "utf8");

describe("the combo route", () => {
  it("parses, with and without a night; a bad date or extra path is not found", () => {
    expect(parse("#/combo")).toEqual({ name: "combo", date: null });
    expect(parse("#/combo?date=2024-11-12")).toEqual({ name: "combo", date: "2024-11-12" });
    expect(parse("#/combo?date=2024-13-45").name).toBe("notfound");
    expect(parse("#/combo/extra").name).toBe("notfound");
  });
  it("has a plain top-level hash in the page and in the artifact frame", () => {
    expect(topHref("combo", false)).toBe("#/combo");
    expect(topHref("combo", true)).toBe("#combo");
    expect(hashForRoute({ name: "combo", date: null }, true)).toBe("#combo");
    expect(hashForRoute({ name: "combo", date: "2024-11-12" }, true)).toBeNull();
  });
});

describe("lens placement: Explore and Market, not Learn", () => {
  it("is searchable in Explore and Market and left out of Learn", () => {
    const href = topHref("combo");
    expect(screensFor(false, SCREEN_ITEMS, true).some((i) => i.href === href)).toBe(true); // Explore
    expect(screensFor(true, SCREEN_ITEMS, true).some((i) => i.href === href)).toBe(true); // Market
    expect(screensFor(false, SCREEN_ITEMS, false).some((i) => i.href === href)).toBe(false); // Learn
  });
  it("the nav hides it in Learn and the page itself says it is an Explore screen", () => {
    expect(read("Shell.tsx")).toMatch(/EXPLORE_UP: readonly TopName\[\] = \["combo"\]/);
    expect(read("Shell.tsx")).toMatch(/lens !== "learn" \|\| !EXPLORE_UP\.includes/);
    const page = read("pages/Combo.tsx");
    expect(page).toContain('lens === "learn"');
    expect(page).toContain("Combo mode is an Explore screen");
  });
  it("is not behind the betting switch", () => {
    expect(read("pages/Combo.tsx")).not.toMatch(/BETTING/);
    expect(read("App.tsx")).toMatch(/case "combo":\s*page = <ComboPage/);
  });
});

describe("a game that has tipped is closed (the tip rule)", () => {
  it("the page checks the server's clock against tip_utc before any tab, and the builder offers open games only", () => {
    const page = read("pages/Combo.tsx");
    expect(page).toContain("hasTipped(game, ctx.clock)");
    expect(page).toContain("CLOSED_SENTENCE");
    expect(read("combo/ComboBuild.tsx")).toMatch(/filter\(\(g\) => !hasTipped\(g, ctx\.clock\)\)/);
  });
  it("the clock comes from the API's answer, not from a guess in the browser", () => {
    expect(read("pages/Combo.tsx")).toContain("env.clock_utc");
    expect(read("pages/Combo.tsx")).not.toMatch(/Date\.now\(|new Date\(\)/);
  });
});
