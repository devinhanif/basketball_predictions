import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { metaText, versionChanged } from "./metaWatch";
import { allTerms, searchTerms, termHref, termsFor } from "./terms";
import { agoText } from "./time";
import { nextSort, sortRows, tabKey, trapIndex } from "./ui/logic";
import { COMPARE_LOOK, EDGE_LABELS, EDGE_LOOK, compareOf, edgeLabelOf } from "./ui/semantic";
import { parse } from "./router";

// The Learn lens rule, the same patterns as scripts/learn-dom.mjs and the route walker.
const PRICE_RE = [
  /(?<![\w])[+\-−]\d{3,4}\b/,
  /\d\s?¢/,
  /\$\s?\d/,
  /\bcents? per\b/i,
  /\b(EV|vig|vigorish|odds|payout|moneyline|implied|break-even)\b/i,
  /\bedges?\b/i,
  /\bfees?\b/i,
  /\bprices?\b/i,
  /\b(DraftKings|FanDuel|BetMGM|Caesars|ESPN ?BET|Fanatics|Kalshi|Polymarket|Novig)\b/i,
  /\bbook \d{1,3}(\.\d)?%/i, // a book's chance beside ours ("book 51%")
];
const priceWord = (s: string): string | null => {
  for (const re of PRICE_RE) {
    const m = re.exec(s);
    if (m) return m[0];
  }
  return null;
};

describe("dialog focus trap", () => {
  it("wraps at both ends and enters from outside", () => {
    expect(trapIndex(3, 2, false)).toBe(0);
    expect(trapIndex(3, 0, true)).toBe(2);
    expect(trapIndex(3, 1, false)).toBe(2);
    expect(trapIndex(3, -1, false)).toBe(0);
    expect(trapIndex(3, -1, true)).toBe(2);
    expect(trapIndex(0, 0, false)).toBe(-1);
  });
});

describe("tabs: roving tabindex keys", () => {
  it("arrows wrap, Home and End jump, other keys do nothing", () => {
    expect(tabKey("ArrowRight", 2, 3)).toBe(0);
    expect(tabKey("ArrowLeft", 0, 3)).toBe(2);
    expect(tabKey("Home", 2, 3)).toBe(0);
    expect(tabKey("End", 0, 3)).toBe(2);
    expect(tabKey("ArrowDown", 0, 3)).toBeNull();
    expect(tabKey("ArrowDown", 0, 3, true)).toBe(1);
    expect(tabKey("a", 0, 3)).toBeNull();
    expect(tabKey("ArrowRight", 0, 0)).toBeNull();
  });
});

describe("table sorting", () => {
  const rows = [{ k: "a", v: 2 }, { k: "b", v: null }, { k: "c", v: 5 }, { k: "d", v: Number.NaN }, { k: "e", v: 2 }];
  it("orders by the stored value, missing last in both directions, stable on ties", () => {
    expect(sortRows(rows, (r) => r.v, "descending").map((r) => r.k)).toEqual(["c", "a", "e", "b", "d"]);
    expect(sortRows(rows, (r) => r.v, "ascending").map((r) => r.k)).toEqual(["a", "e", "c", "b", "d"]);
  });
  it("flips the same column, starts a new one at its own first direction", () => {
    expect(nextSort(null, "x")).toEqual({ key: "x", dir: "descending" });
    expect(nextSort({ key: "x", dir: "descending" }, "x")).toEqual({ key: "x", dir: "ascending" });
    expect(nextSort({ key: "x", dir: "ascending" }, "y", "ascending")).toEqual({ key: "y", dir: "ascending" });
  });
});

describe("edge labels: one mapping, never colour alone", () => {
  it("each of the four has a distinct role, icon and word, a meaning and a Key terms anchor", () => {
    const looks = EDGE_LABELS.map((l) => EDGE_LOOK[l]);
    for (const k of ["role", "icon", "word", "term"] as const) expect(new Set(looks.map((x) => x[k])).size).toBe(4);
    for (const l of looks) {
      expect(l.meaning.length).toBeGreaterThan(30);
      expect(allTerms().some((t) => t.id === l.term)).toBe(true);
    }
  });
  it("reads stored labels without guessing", () => {
    expect(edgeLabelOf("strong")).toBe("strong");
    expect(edgeLabelOf("THIN")).toBe("thin");
    expect(edgeLabelOf(null)).toBeNull();
    expect(edgeLabelOf("")).toBe("none");
    expect(edgeLabelOf("arb_candidate")).toBeNull();
    expect(edgeLabelOf("daily_dollar")).toBeNull();
  });
  it("better / worse / same carry an icon and a word each", () => {
    expect(new Set(Object.values(COMPARE_LOOK).map((l) => l.icon)).size).toBe(3);
    expect(compareOf(-0.01, true)).toBe("better");
    expect(compareOf(0.01, true)).toBe("worse");
    expect(compareOf(0.01, false)).toBe("better");
    expect(compareOf(0.004, true, 0.005)).toBe("same");
    expect(compareOf(Number.NaN, true)).toBe("same");
  });
});

describe("key terms", () => {
  it("has the glossary's terms, each with a unique anchor that routes", () => {
    const ids = allTerms().map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ["t-minus", "made-at", "as-of", "crps", "brier", "clv", "de-vig", "holdout", "prereg", "replay-oracle", "shadow", "dnp-rule", "p-play", "quantile-grid", "n-interval", "lenses", "label-strong"]) {
      expect(ids).toContain(id);
      const r = parse(termHref(id));
      expect(r).toEqual({ name: "terms", term: id, q: null });
    }
  });
  it("outside the Market lens, no term uses a price word (the Learn rule)", () => {
    for (const t of termsFor(false)) expect(priceWord(`${t.term} ${t.says}`), t.id).toBeNull();
    expect(termsFor(true).length).toBeGreaterThan(termsFor(false).length);
  });
  it("search needs every word", () => {
    expect(searchTerms(allTerms(), "holdout").map((t) => t.id)).toEqual(["holdout"]);
    expect(searchTerms(allTerms(), "").length).toBe(allTerms().length);
    expect(searchTerms(allTerms(), "zzzz")).toEqual([]);
  });
});

describe("first-visit intro and shell text", () => {
  it("the intro has five points, no price words, and does not cover the page", () => {
    const text = readFileSync("src/Welcome.tsx", "utf8");
    expect(text.match(/<li>/g)?.length).toBe(5);
    expect(text).not.toContain('aria-modal="true"');
    const visible = text.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "").replace(/<[^>]+>/g, " ");
    expect(priceWord(visible.replace(/class="[^"]*"/g, ""))).toBeNull();
  });
});

describe("the data line", () => {
  const now = new Date("2026-10-17T12:00:00Z");
  it("says plainly when the data is a replay", () => {
    const r = metaText({ mode: "fixture_replay", mode_note: "x", data_as_of: "2026-10-17T10:00:00Z", data_version: "v" }, false, now);
    expect(r.kind).toBe("replay");
    expect(r.text).toContain("replay of real 2024-25 games");
    expect(r.text).toContain("2 h ago");
  });
  it("gives the as-of time in Central with its age", () => {
    const r = metaText({ mode: "export", mode_note: "Nightly export.", data_as_of: "2026-10-17T11:30:00Z", data_version: "v" }, false, now);
    expect(r.text).toBe("Data as of Oct 17, 6:30 AM CT, 30 min ago. Nightly export.");
  });
  it("says the artifact does not update, and flags an unreadable answer", () => {
    expect(metaText({ mode: "export", mode_note: "n", data_as_of: "2026-10-17T11:30:00Z" }, true, now).text).toMatch(/^A saved snapshot/);
    expect(metaText(null, false, now).kind).toBe("problem");
    expect(metaText({ data_as_of: 5 }, false, now).kind).toBe("problem");
  });
  it("notices a new data_version only when both are real and differ", () => {
    expect(versionChanged("a", "b")).toBe(true);
    expect(versionChanged("a", "a")).toBe(false);
    expect(versionChanged(null, "b")).toBe(false);
    expect(versionChanged("a", "")).toBe(false);
    expect(versionChanged("a", undefined)).toBe(false);
  });
  it("ages read in plain words", () => {
    expect(agoText("2026-10-17T11:59:40Z", now)).toBe("just now");
    expect(agoText("2026-10-17T11:48:00Z", now)).toBe("12 min ago");
    expect(agoText("2026-10-16T12:00:00Z", now)).toBe("24 h ago");
    expect(agoText("2026-10-14T12:00:00Z", now)).toBe("3 days ago");
    expect(agoText("garbage", now)).toBeNull();
    expect(agoText("2026-10-17T13:00:00Z", now)).toBeNull();
  });
});
