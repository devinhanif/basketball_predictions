import { describe, expect, it } from "vitest";
import { bookChance, clampLine, ladderAt, modelPct, nText, ofText, ordinal, rangeText, rankPhrase, rowHits, rowModel, rowPrice, snapHalf } from "./leg";
import type { LadderRow, Tally } from "./legTypes";

const row = (line: number): LadderRow => ({
  line,
  l10_over: 6,
  l10_under: 3,
  l10_push: 1,
  l5_over: 2,
  l5_under: 3,
  l5_push: 0,
  model_over: 0.45,
  model_under: 0.55,
  over_price: 105,
  under_price: null,
});

const tally = (n: number, hits: number): Tally => ({
  n,
  hits,
  misses: n - hits,
  pushes: 0,
  rate: n === 0 ? null : hits / n,
  ci_lo: n === 0 ? null : 0.3,
  ci_hi: n === 0 ? null : 0.83,
  avg: null,
  avg_minutes: null,
  small: n < 10,
});

describe("ladder reads", () => {
  it("finds only an exact line", () => {
    const ladder = [row(21.5), row(22.5)];
    expect(ladderAt(ladder, 22.5)?.line).toBe(22.5);
    expect(ladderAt(ladder, 22)).toBeNull();
  });

  it("switches between sides and windows", () => {
    const r = row(22.5);
    expect(rowHits(r, "over", 10)).toBe(6);
    expect(rowHits(r, "under", 10)).toBe(3);
    expect(rowHits(r, "over", 5)).toBe(2);
    expect(rowHits(r, "under", 5)).toBe(3);
    expect(rowModel(r, "under")).toBe(0.55);
    expect(rowPrice(r, "over")).toBe(105);
    expect(rowPrice(r, "under")).toBeNull();
  });
});

describe("words and numbers", () => {
  it("prints hits of games, the interval and the sample size", () => {
    expect(ofText(6, 10)).toBe("6 of 10");
    expect(rangeText(tally(10, 6))).toBe("30% to 83%");
    expect(rangeText(tally(0, 0))).toBeNull();
    expect(nText(tally(4, 1))).toBe("n = 4, small sample");
    expect(nText(tally(10, 6))).toBe("n = 10");
  });

  it("model chances read as bounds at the ends of the stored grid", () => {
    expect(modelPct(0.4526)).toBe("45%");
    expect(modelPct(0.95)).toBe("95%+");
    expect(modelPct(0.05)).toBe("<5%");
    expect(modelPct(0.5)).toBe("50%");
  });

  it("ordinals", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 30].map(ordinal)).toEqual(["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "30th"]);
  });

  it("ranks: 1 allows the most", () => {
    expect(rankPhrase(3, 30)).toContain("soft spot");
    expect(rankPhrase(28, 30)).toContain("tough spot");
    expect(rankPhrase(13, 30)).toBe("13th most of 30");
  });

  it("clamps and snaps lines", () => {
    expect(clampLine(40, 10.5, 30.5)).toBe(30.5);
    expect(clampLine(5, 10.5, 30.5)).toBe(10.5);
    expect(snapHalf(22.3)).toBe(22.5);
    expect(snapHalf(22.2)).toBe(22);
  });
});

describe("F13: the book's chance next to the model's", () => {
  const prices = { posted_line: 28.5, over_price: -110, under_price: -110, on_posted_line: true, snapshot_utc: null, say: "", books: [], market_p_over: 0.49, market_book: "DraftKings" };
  it("uses the API's de-vigged number on the line on screen, and flips it for the under", () => {
    expect(bookChance(prices, true, null, "over")).toEqual({ p: 0.49, book: "DraftKings" });
    expect(bookChance(prices, true, null, "under")?.p).toBeCloseTo(0.51);
  });
  it("de-vigs a slider line's own stored pair", () => {
    const r = { ...row(29.5), over_price: -110, under_price: -110 };
    expect(bookChance(prices, false, r, "over")?.p).toBeCloseTo(0.5);
  });
  it("shows nothing when no single book quoted both sides", () => {
    expect(bookChance({ ...prices, market_book: null, market_p_over: null }, true, null, "over")).toBeNull();
    expect(bookChance(prices, false, row(29.5), "over")).toBeNull(); // row has only an over price
  });
});
