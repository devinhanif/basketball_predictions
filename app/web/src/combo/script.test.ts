import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import night from "../../../fixtures/combo/combo_2024-11-12.json";
import { candidateLegs, evaluateGameLegs, scriptPrior, statEntries } from "./engine";
import { calibratedOnly } from "./ComboLift";
import { MAIN_LINE_ONLY, hasTipped, histLine, lineInRange, lineMove, offerableLines, postedLineCount, readHist, scriptSentence, scriptView, scriptWords } from "./script";
import type { ComboPayload, PlayerLeg } from "./types";

const P = night as unknown as ComboPayload;
const G = P.games[0];
if (G === undefined) throw new Error("fixture has no game");
const shared = P.shared;

describe("the fixture night follows the contract", () => {
  it("has at least two games, pre-2025 dates, no season 2025", () => {
    expect(P.schema).toBe("combo_export_v1");
    expect(P.games.length).toBeGreaterThanOrEqual(2);
    expect(P.night < "2025-01-01").toBe(true);
    for (const g of P.games) expect(g.date < "2025-01-01").toBe(true);
  });
  it("carries no price, fee, stake or EV key anywhere", () => {
    const bad = /(^|_)(price|prices|odds|fee|fees|stake|stakes|ev|edge|kelly|vig)(_|$)/i;
    const walk = (x: unknown): string[] =>
      Array.isArray(x) ? x.flatMap(walk) : typeof x === "object" && x !== null ? Object.entries(x).flatMap(([k, v]) => [k, ...walk(v)]) : [];
    expect(walk(P).filter((k) => bad.test(k))).toEqual([]);
  });
});

describe("script view", () => {
  for (const s of ["blowout", "close", "shootout", "grind"] as const) {
    it(`${s}: every row is a calibrated main-line leg and the lists are sorted by the size of the change`, () => {
      const v = scriptView(G, shared, s);
      expect(v.more.length + v.less.length).toBeGreaterThan(0);
      for (const r of [...v.more, ...v.less]) {
        const grid = r.player.stats[r.stat as "pts"];
        expect(grid).toBeDefined();
        expect(lineInRange(grid as NonNullable<typeof grid>, r.line)).toBe(true);
        expect(r.line).toBe(grid?.main_line);
        expect(r.change).toBeCloseTo(r.p_script - r.p_now, 12);
        // the "now" number is the engine's marginal; the script number is the engine's conditional marginal
        const leg: PlayerLeg = { kind: "player", game_id: G.game_id, player_id: r.player.player_id, stat: r.stat, line: r.line, side: r.side };
        expect(r.p_script).toBeCloseTo(evaluateGameLegs([leg], G, shared, s).p_joint, 12);
      }
      expect(v.more.every((r) => r.change > 0)).toBe(true);
      expect(v.less.every((r) => r.change < 0)).toBe(true);
      const sizes = v.more.map((r) => Math.abs(r.change));
      expect(sizes.every((x, i) => i === 0 || x <= (sizes[i - 1] ?? Infinity))).toBe(true);
      expect(v.prior).toBeCloseTo(scriptPrior(s, G, shared), 12);
    });
  }
  it("a blowout makes starters' overs harder and bench overs easier, and the sentence says so from the numbers", () => {
    const v = scriptView(G, shared, "blowout");
    expect((v.starters?.after ?? 1) < (v.starters?.before ?? 0)).toBe(true);
    expect((v.bench?.after ?? 0) > (v.bench?.before ?? 1)).toBe(true);
    const text = scriptSentence("blowout", v) ?? "";
    expect(text).toMatch(/starters' overs get harder/);
    expect(text).toMatch(/bench overs get easier/);
  });
  it("the sentence follows the sign: reversed numbers give reversed words", () => {
    const v = scriptView(G, shared, "blowout");
    const flipped = { ...v, starters: v.starters && { ...v.starters, before: v.starters.after, after: v.starters.before } };
    expect(scriptSentence("blowout", flipped)).toMatch(/starters' overs get easier/);
    const still = { ...v, starters: v.starters && { ...v.starters, after: v.starters.before }, bench: null };
    expect(scriptSentence("blowout", still)).toMatch(/barely move/);
  });
  it("lines outside the calibrated range are left out and counted", () => {
    const g = structuredClone(G);
    const p = g.players[0];
    const grid = p?.stats.pts;
    if (grid === undefined) throw new Error("no grid");
    grid.cal_k = [grid.cal_k[1] + 5, grid.cal_k[1] + 9] as [number, number]; // main line now outside
    const before = scriptView(G, shared, "blowout");
    const after = scriptView(g, shared, "blowout");
    expect(after.dropped).toBe(before.dropped + 1);
    expect([...after.more, ...after.less].some((r) => r.player.player_id === p?.player_id && r.stat === "pts")).toBe(false);
    expect(calibratedOnly(g).dropped).toBe(calibratedOnly(G).dropped + 1);
    expect(calibratedOnly(g).game.players[0]?.stats.pts?.main_line).toBeNull();
  });
  it("the pickers offer the posted main line only (never another rung), inside the calibrated range", () => {
    expect(MAIN_LINE_ONLY).toBe(true);
    let seen = 0;
    for (const g of P.games) {
      for (const p of g.players) {
        for (const [, grid] of statEntries(p)) {
          const lines = offerableLines(grid);
          expect(lines.length).toBeLessThanOrEqual(1);
          for (const l of lines) {
            expect(l).toBe(grid.main_line);
            expect(lineInRange(grid, l)).toBe(true);
            seen += 1;
          }
        }
      }
      // target search candidates are posted lines too, over or under
      for (const c of candidateLegs(g, 0.35, 0.75, "p_band", true, true)) {
        const grid = g.players.find((p) => p.player_id === c.player_id)?.stats[c.stat as "pts"];
        expect(c.line).toBe(grid?.main_line);
        expect(["over", "under"]).toContain(c.side);
      }
    }
    expect(seen).toBeGreaterThan(0);
  });
  it("a stat with no posted line, or one outside the range, offers nothing; a game with no posted line is thin", () => {
    const grid = structuredClone(G.players[0]?.stats.pts);
    if (grid === undefined) throw new Error("no grid");
    expect(offerableLines({ ...grid, main_line: null })).toEqual([]);
    expect(offerableLines({ ...grid, cal_k: [grid.cal_k[1] + 3, grid.cal_k[1] + 4] })).toEqual([]);
    const bare = structuredClone(G);
    for (const p of bare.players) for (const e of Object.values(p.stats)) e.main_line = null;
    expect(postedLineCount(bare)).toBe(0);
    expect(postedLineCount(G)).toBeGreaterThan(0);
    const v = scriptView(bare, shared, "blowout");
    expect(v.more.length + v.less.length).toBe(0);
  });
  it("describes each script from the payload's own thresholds", () => {
    expect(scriptWords("blowout", G, shared)).toBe("decided by 15 or more");
    expect(scriptWords("close", G, shared)).toBe("decided by 5 or fewer");
    expect(scriptWords("shootout", G, shared)).toMatch(/^\d+ or more total points$/);
    expect(scriptWords("grind", G, shared)).toMatch(/^\d+ or fewer total points$/);
  });
});

describe("stored season history (shown beside, never mixed in)", () => {
  const h = readHist({ n_all: 12, k_all: 7, blowout: [3, 2], close: [4, 1], shootout: [0, 0], extra: 1 });
  it("reads tolerantly: absent, partial, extra keys, malformed pairs", () => {
    expect(readHist(undefined)).toBeNull();
    expect(readHist(null)).toBeNull();
    expect(readHist({})).toBeNull();
    expect(readHist({ blowout: [3, 5] })).toBeNull(); // k over n is nonsense: dropped
    expect(readHist({ blowout: [3, "x"] })).toBeNull();
    expect(readHist({ n_all: 4, k_all: 9 })?.k_all).toBeNull();
    expect(h?.by.close).toEqual([4, 1]);
  });
  it("always shows both numbers of the pair and says this season", () => {
    const l = histLine(h, "blowout", "over", 24.5);
    expect(l?.text).toBe("In blowouts this season: over 24.5 in 2 of 3 games.");
    expect(l?.all).toBe("All games this season: 7 of 12.");
    expect(l?.few).toBe(true); // n under 5
    expect(histLine(h, "blowout", "under", 24.5)?.text).toBe("In blowouts this season: under 24.5 in 1 of 3 games.");
  });
  it("n of 0 says so instead of hiding the row; a missing script shows only the all-games line", () => {
    expect(histLine(h, "shootout", "over", 24.5)?.text).toBe("No shootouts yet this season.");
    expect(histLine(h, "grind", "over", 24.5)).toEqual({ text: "", all: "All games this season: 7 of 12.", few: false });
    expect(histLine(null, "grind", "over", 24.5)).toBeNull();
  });
  it("n of 5 or more is not marked few", () => {
    expect(histLine(readHist({ blowout: [6, 4] }), "blowout", "over", 5.5)?.few).toBe(false);
  });
  it("history never changes a chance or the order: the view is the same without it", () => {
    const stripped = structuredClone(G);
    for (const p of stripped.players) for (const g of Object.values(p.stats)) delete g.hist;
    const a = scriptView(G, shared, "close");
    const b = scriptView(stripped, shared, "close");
    expect(a.more.map((r) => [r.player.player_id, r.stat, r.side, r.p_script])).toEqual(b.more.map((r) => [r.player.player_id, r.stat, r.side, r.p_script]));
    expect(a.less.map((r) => [r.player.player_id, r.stat, r.side])).toEqual(b.less.map((r) => [r.player.player_id, r.stat, r.side]));
    expect(b.more.every((r) => r.hist === null)).toBe(true);
  });
});

describe("the tip rule and the line number", () => {
  it("a game at or after tip is closed; an unreadable time is closed", () => {
    expect(hasTipped({ tip_utc: "2024-11-13T00:00:00Z" }, "2024-11-12T23:30:00+00:00")).toBe(false);
    expect(hasTipped({ tip_utc: "2024-11-13T00:00:00Z" }, "2024-11-13T00:00:00+00:00")).toBe(true);
    expect(hasTipped({ tip_utc: "2024-11-13T00:00:00Z" }, "2024-11-13T01:00:00Z")).toBe(true);
    expect(hasTipped({ tip_utc: "nonsense" }, "2024-11-12T00:00:00Z")).toBe(true);
  });
  it("line movement is two numbers in words, nothing else", () => {
    expect(lineMove({ k0: 1, surv: [], main_line: 24.5, main_line_open: 23.5, cal_k: [1, 2] })).toBe("line opened 23.5, now 24.5");
    expect(lineMove({ k0: 1, surv: [], main_line: 24.5, cal_k: [1, 2] })).toBeNull();
  });
});

describe("what the combo screen may not contain (source checks)", () => {
  const dir = import.meta.dirname;
  const files = [
    ...readdirSync(dir)
      .filter((f) => /\.(ts|tsx|css)$/.test(f) && !f.endsWith(".test.ts"))
      .map((f) => join(dir, f)),
    join(dir, "../pages/Combo.tsx"),
  ];
  const text = (f: string): string => readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  it("never reads or shows the uncalibrated number", () => {
    for (const f of files.filter((x) => !x.endsWith("engine.ts") && !x.endsWith("types.ts"))) expect(text(f), f).not.toMatch(/raw_at_main/);
  });
  it("has no price, fee, stake, EV or odds anywhere on its screens", () => {
    for (const f of files.filter((x) => x.endsWith(".tsx"))) {
      expect(text(f), f).not.toMatch(/\b(price|prices|priced|odds|fee|fees|stake|stakes|wager|bankroll|kelly|american|payout)\b/i);
      expect(text(f), f).not.toMatch(/\bEV\b|expected value|positive.ev/i);
    }
  });
  it("never calls a combo calibrated and never puts a result beside a chance (no result or outcome fields read)", () => {
    for (const f of files.filter((x) => x.endsWith(".tsx"))) {
      expect(text(f), f).not.toMatch(/\.actual\b|\.result\b|went_over|api\.(results|gameResult|record)\b/);
    }
  });
  it("is not behind the betting switch and imports nothing from the betting module", () => {
    for (const f of files) expect(text(f), f).not.toMatch(/BETTING|\.\.\/betting|features"/);
  });
  it("shows the label for three or more entities and the word exploratory beside every script", () => {
    const bits = text(join(dir, "Bits.tsx"));
    expect(bits).toContain("r.label");
    const sc = text(join(dir, "ComboScript.tsx"));
    expect(sc).toContain("statusWord(shared, s)");
    expect(sc).toContain("Exploratory");
  });
});
