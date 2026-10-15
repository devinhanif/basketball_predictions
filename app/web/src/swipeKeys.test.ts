import { describe, expect, it } from "vitest";
import { swipeKeyChoice } from "./swipeKeys";
import { readFileSync } from "node:fs";

describe("F12: swipe keys", () => {
  it("answers only when the deck itself has focus", () => {
    expect(swipeKeyChoice("ArrowRight", true)).toBe("over");
    expect(swipeKeyChoice("ArrowLeft", true)).toBe("under");
    expect(swipeKeyChoice(" ", true)).toBe("skip");
    expect(swipeKeyChoice("s", true)).toBe("skip");
    for (const k of ["ArrowRight", "ArrowLeft", " ", "s", "ArrowDown"]) {
      expect(swipeKeyChoice(k, false)).toBeNull();
    }
  });

  it("leaves ArrowDown to scrolling, and ignores every other key", () => {
    expect(swipeKeyChoice("ArrowDown", true)).toBeNull();
    expect(swipeKeyChoice("ArrowUp", true)).toBeNull();
    expect(swipeKeyChoice("Enter", true)).toBeNull();
    expect(swipeKeyChoice("a", true)).toBeNull();
  });
});

describe("F06: Over and Under carry equal visual weight", () => {
  const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  const rule = (sel: string): string => {
    const m = new RegExp(`(?:^|\\n)${sel.replace(/\./g, "\\.")} \\{([^}]*)\\}`).exec(css);
    return (m?.[1] ?? "").replace(/\s+/g, " ").trim();
  };
  it("styles the two buttons identically", () => {
    expect(rule(".btn.over")).not.toBe("");
    expect(rule(".btn.over")).toBe(rule(".btn.under"));
  });
  it("styles the two drag stamps and lean borders the same colour", () => {
    expect(rule(".swipe-card.lean-over")).toBe(rule(".swipe-card.lean-under"));
    const color = (s: string): string => /color: ([^;]+);/.exec(s)?.[1] ?? "";
    expect(color(rule(".stamp.over"))).toBe(color(rule(".stamp.under")));
    expect(color(rule(".stamp.over"))).not.toBe("");
  });
});
