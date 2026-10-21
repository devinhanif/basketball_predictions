import { describe, expect, it } from "vitest";
import { BUILDS, type BuildCard, draftText, isThin, leaguePct, levelOf, ordinal, potentialText, zonePct } from "./build";

describe("player build fixture", () => {
  it("is dated before 2025 and says where each number comes from", () => {
    expect(BUILDS.meta.as_of < "2025-01-01").toBe(true);
    expect(Object.keys(BUILDS.meta.sources).length).toBeGreaterThanOrEqual(3);
    expect(BUILDS.cards.length).toBeGreaterThanOrEqual(4);
  });
  it("derives each level from the committed cut points", () => {
    const cuts = BUILDS.meta.level_cut_points_minutes_share;
    expect(cuts.length).toBe(4);
    expect([...cuts].sort((a, b) => a - b)).toEqual(cuts);
    for (const c of BUILDS.cards) expect(levelOf(c.minutes_share, cuts), c.name).toBe(c.level);
  });
  it("prints a sample size for every bar and zone", () => {
    for (const c of BUILDS.cards) {
      expect(c.bars.length).toBe(7);
      for (const b of c.bars) expect(b.n_games).toBe(c.n_games_window);
      for (const z of c.zones) expect(z.att).toBeGreaterThanOrEqual(0);
    }
  });
  it("leaves the class empty until the clustering exists", () => {
    for (const c of BUILDS.cards) expect(c.class).toBeNull();
  });
  it("borrows peers only for thin samples", () => {
    for (const c of BUILDS.cards) expect(c.borrowed !== null).toBe(isThin(c) && c.borrowed !== null);
    expect(BUILDS.cards.some((c) => c.borrowed !== null)).toBe(true);
  });
});

describe("helpers", () => {
  it("never reports a 0th or 100th percentile", () => {
    expect(ordinal(100)).toBe("99th");
    expect(ordinal(0)).toBe("1st");
    expect(ordinal(52)).toBe("52nd");
    expect(ordinal(83)).toBe("83rd");
    expect(ordinal(11)).toBe("11th");
  });
  it("handles empty zones", () => {
    const z = { zone: "x", att: 0, made: 0, league_att: 10, league_made: 4 };
    expect(zonePct(z)).toBeNull();
    expect(leaguePct(z)).toBe(0.4);
  });
  it("says so when there is no range", () => {
    expect(potentialText(null)).toMatch(/Not enough/);
    expect(draftText({ ...(BUILDS.cards[0] as BuildCard), draft_pick: null, draft_year: null, draft_status: "undrafted" })).toBe("Undrafted");
  });
});
