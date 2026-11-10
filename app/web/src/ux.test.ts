import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (f: string): string => readFileSync(`src/${f}`, "utf8");

describe("UX pass: the honesty rules survive the reshuffle", () => {
  it("Discover shows a live state word and clock, never a score, next to the chances", () => {
    const text = read("pages/Today.tsx");
    const chip = text.slice(text.indexOf("function StateChip"), text.indexOf("function LiveStrip"));
    expect(chip).not.toMatch(/\.score|\bscore\b|\.pts|home\.|away\./);
    const strip = text.slice(text.indexOf("function LiveStrip"), text.indexOf("function GameRow"));
    expect(strip).not.toMatch(/\.score|\bscore\b/);
  });
  it("Discover keeps the keep-your-money line in every lens and the live strip links to the live screen", () => {
    const text = read("pages/Today.tsx");
    expect(text).toContain("Keep your money.");
    expect(text).toContain("liveHref(");
  });
  it("the loading note says what is loading and warns about the slow first load", () => {
    const text = read("components.tsx");
    expect(text).toContain("from the nightly export");
    expect(text).toContain("can take up to 15 seconds");
  });
  it("the welcome is one screen with one primary action", () => {
    const text = read("Welcome.tsx");
    expect(text).toContain("See tonight's games");
  });
  it("tap targets are raised to 44 px in the UX stylesheet", () => {
    const css = read("ux.css");
    expect(css).toMatch(/\.seg-btn\s*\{\s*min-height: 44px/);
    expect(css).toMatch(/\.tab\s*\{\s*min-height: 44px/);
  });
});
