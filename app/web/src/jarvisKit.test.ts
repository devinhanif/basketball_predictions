// The Jarvis-look components' logic: provenance and freshness badges, the state family, the side panel's layout and its
// slot.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { FRESH_LOOK, PROV_LOOK, PROVENANCES, SIDEPANEL_RAIL_MIN, STATE_KINDS, STATE_LOOK, freshness, panelIsModal, panelLayout, provenanceOf } from "./ui/logic";
import { clearSidePanel, getSidePanel, setSidePanel, subscribeSidePanel } from "./ui/sidePanelStore";

const NOW = new Date("2026-11-17T05:00:00Z");

describe("provenance", () => {
  it("knows exactly the four sources, case-insensitively, and nothing else", () => {
    expect(PROVENANCES).toEqual(["live", "delayed", "manual", "sample"]);
    expect(provenanceOf("LIVE")).toBe("live");
    expect(provenanceOf(" sample ")).toBe("sample");
    expect(provenanceOf("illustrative")).toBeNull();
    expect(provenanceOf(null)).toBeNull();
    expect(provenanceOf(undefined)).toBeNull();
  });
  it("every source has a word, an icon and a meaning; sample says it is not real", () => {
    for (const p of PROVENANCES) {
      expect(PROV_LOOK[p].word.length).toBeGreaterThan(2);
      expect(PROV_LOOK[p].icon.length).toBeGreaterThan(0);
      expect(PROV_LOOK[p].meaning.length).toBeGreaterThan(10);
    }
    expect(PROV_LOOK.sample.meaning).toMatch(/not real/i);
  });
});

describe("freshness", () => {
  it("is fresh under the limit, old over it, and always carries the age", () => {
    expect(freshness("2026-11-17T01:00:00Z", 12, NOW)).toEqual({ state: "fresh", ago: "4 h ago" });
    expect(freshness("2026-11-14T05:00:00Z", 12, NOW)).toEqual({ state: "old", ago: "3 days ago" });
  });
  it("is unknown for a missing, unreadable or future time (never 'fresh' by default)", () => {
    expect(freshness(null, 12, NOW).state).toBe("unknown");
    expect(freshness("", 12, NOW).state).toBe("unknown");
    expect(freshness("not a time", 12, NOW).state).toBe("unknown");
    expect(freshness("2026-11-18T05:00:00Z", 12, NOW).state).toBe("unknown");
    expect(FRESH_LOOK.unknown.word).toBe("Time not reported");
  });
});

describe("state family", () => {
  it("has the six states, each with a word, an icon, a title and a sentence", () => {
    expect(STATE_KINDS).toEqual(["loading", "empty", "stale", "partial", "error", "reconnecting"]);
    const words = new Set<string>();
    for (const k of STATE_KINDS) {
      const l = STATE_LOOK[k];
      expect(l.word.length).toBeGreaterThan(3);
      expect(l.title.length).toBeGreaterThan(3);
      expect(l.meaning.length).toBeGreaterThan(10);
      words.add(l.word);
    }
    expect(words.size).toBe(STATE_KINDS.length);
    expect(STATE_LOOK.error.role).toBe("alert");
  });
  it("the error state is the existing Problem screen, not a second one", () => {
    const src = readFileSync(join(__dirname, "ui", "State.tsx"), "utf8");
    expect(src).toContain('from "../Problem"');
    expect(src).toContain("<ProblemPanel");
  });
});

describe("side panel layout", () => {
  it("costs nothing when empty, at any width", () => {
    expect(panelLayout(390, false, true)).toBe("none");
    expect(panelLayout(1920, false, false)).toBe("none");
  });
  it("is a rail from 1280 px, a sheet below", () => {
    expect(SIDEPANEL_RAIL_MIN).toBe(1280);
    expect(panelLayout(1280, true, false)).toBe("rail");
    expect(panelLayout(1279, true, false)).toBe("sheet-closed");
    expect(panelLayout(375, true, true)).toBe("sheet-open");
  });
  it("only an open sheet is modal (focus trap, scroll lock)", () => {
    expect(panelIsModal("sheet-open")).toBe(true);
    expect(panelIsModal("sheet-closed")).toBe(false);
    expect(panelIsModal("rail")).toBe(false);
    expect(panelIsModal("none")).toBe(false);
  });
});

describe("side panel slot", () => {
  afterEach(() => {
    clearSidePanel("a");
    clearSidePanel("b");
  });
  it("starts empty, holds one owner, and an owner clears only its own", () => {
    expect(getSidePanel()).toBeNull();
    let n = 0;
    const off = subscribeSidePanel(() => {
      n++;
    });
    setSidePanel({ owner: "a", title: "A", content: () => null });
    setSidePanel({ owner: "b", title: "B", content: () => null });
    expect(getSidePanel()?.owner).toBe("b");
    clearSidePanel("a");
    expect(getSidePanel()?.owner).toBe("b");
    clearSidePanel("b");
    expect(getSidePanel()).toBeNull();
    off();
    setSidePanel({ owner: "a", title: "A", content: () => null });
    expect(n).toBe(3);
  });
});
