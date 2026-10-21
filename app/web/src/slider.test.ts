import { describe, expect, it } from "vitest";
import { pct } from "./format";
import {
  CAL_NOT_EXPORTED,
  CAL_NO_LINE,
  CAL_OUT_OF_FIT,
  CAL_OUT_OF_RANGE,
  CAL_LABEL,
  CAL_WHY,
  RAW_LABEL,
  pairLines,
  MODEL_ONLY,
  TAIL_NOTE,
  calibratedLabel,
  isHalfPoint,
  isTail,
  modelLine,
  pGe,
  pOver,
  pUnder,
  percentAlong,
  quantile,
  read,
  sliderRange,
  snapHalf,
  type SliderInput,
} from "./slider";
import type { RungCal } from "./types";

// P(Y >= k) for k = 0..6 of a table built from counts out of 100: Y = 0:5, 1:10, 2:20, 3:30, 4:20, 5:10, 6:5.
const COUNTS = [5, 10, 20, 30, 20, 10, 5];
const PGE: number[] = COUNTS.reduce<number[]>((acc, c) => [...acc, (acc[acc.length - 1] ?? 1) - c / 100], [1]).slice(0, 7);

describe("grid sums and half-point handling", () => {
  it("builds P(Y >= k) correctly", () => {
    expect(PGE[0]).toBe(1);
    expect(PGE[1]).toBeCloseTo(0.95);
    expect(PGE[4]).toBeCloseTo(0.35);
    expect(PGE[6]).toBeCloseTo(0.05);
  });

  it("P(over k.5) = P(Y >= k + 1), matched against a direct sum of the point masses", () => {
    for (let k = 0; k <= 6; k++) {
      const direct = COUNTS.slice(k + 1).reduce((a, b) => a + b, 0) / 100;
      expect(pOver(PGE, k + 0.5)).toBeCloseTo(direct, 12);
      expect(pUnder(PGE, k + 0.5)).toBeCloseTo(1 - direct, 12);
    }
  });

  it("is zero past the table and one below zero", () => {
    expect(pOver(PGE, 40.5)).toBe(0);
    expect(pGe(PGE, 0)).toBe(1);
    expect(pGe(PGE, -3)).toBe(1);
    expect(pOver(PGE, -0.5)).toBe(1);
  });

  it("P(over) is monotone non-increasing in the line", () => {
    let prev = 1;
    for (let l = -0.5; l < 12; l += 1) {
      const p = pOver(PGE, l);
      expect(p).toBeLessThanOrEqual(prev + 1e-12);
      prev = p;
    }
  });

  it("refuses a whole-number line and snaps to the half-point grid", () => {
    expect(() => pOver(PGE, 3)).toThrow();
    expect(isHalfPoint(3.5)).toBe(true);
    expect(isHalfPoint(3)).toBe(false);
    expect(snapHalf(3.5)).toBe(3.5);
    expect(snapHalf(3.2)).toBe(3.5);
    expect(snapHalf(2.9)).toBe(2.5);
    expect(snapHalf(3.9)).toBe(3.5);
    expect(snapHalf(4)).toBe(4.5);
  });
});

describe("the model's line and the tails", () => {
  it("finds the half-point line closest to 50/50", () => {
    // P(over 2.5) = 0.65... table: p_ge[3] = 0.65 ; P(over 3.5) = 0.35 ; tie at distance 0.15 -> lower line
    expect(modelLine(PGE)).toBe(2.5);
    expect(modelLine([1, 0.5, 0.1])).toBe(0.5);
  });

  it("uses whole-count percentiles", () => {
    expect(quantile(PGE, 0.1)).toBe(1);
    expect(quantile(PGE, 0.9)).toBe(5);
  });

  it("flags lines beyond the 10th to 90th percentile only", () => {
    expect(isTail(PGE, 0.5)).toBe(true);
    expect(isTail(PGE, 1.5)).toBe(false);
    expect(isTail(PGE, 4.5)).toBe(false);
    expect(isTail(PGE, 5.5)).toBe(true);
  });
});

const RUNG: RungCal = {
  book: "draftkings",
  main_line: 2.5,
  price_at: "2024-11-12T22:55:00Z",
  map_version: "rung_cal_map_v1",
  map_sha8: "116f4480",
  lines: [
    { line: 1.5, p_over: 0.8 },
    { line: 2.5, p_over: 0.6 },
  ],
};
const base: SliderInput = { p_ge: PGE, method: "exact", posted_line: 2.5, book_main_line: 2.5, rung_cal: RUNG };

describe("the honesty labels", () => {
  it("uses rung_cal at a listed line and names the map", () => {
    const r = read(base, 2.5);
    expect(r.source).toBe("rung_cal");
    expect(r.p_over).toBe(0.6);
    expect(r.p_under).toBeCloseTo(0.4);
    expect(r.label).toBe(calibratedLabel("rung_cal_map_v1"));
    expect(r.label).toContain("calibrated using the book's main line (T494)");
    expect(r.is_posted && r.is_book_main).toBe(true);
  });

  it("falls back to the raw model, labelled model only, at an unlisted line", () => {
    const r = read(base, 3.5); // higher than every listed line: raw P(over) < 0.5 is never listed
    expect(r.source).toBe("model");
    expect(r.p_over).toBeCloseTo(0.35);
    expect(r.label).toBe(MODEL_ONLY);
    expect(r.label).toContain("model only, not calibrated off the main line");
    expect(r.label).not.toContain("calibrated using");
  });

  it("with no rung_cal at all, even the posted line is model only", () => {
    const r = read({ ...base, rung_cal: null }, 2.5);
    expect(r.source).toBe("model");
    expect(r.p_over).toBeCloseTo(0.65);
    expect(r.label).toBe(MODEL_ONLY);
  });

  it("adds the tail note beyond the 10th-90th percentile, calibrated or not", () => {
    const low = read({ ...base, rung_cal: { ...RUNG, lines: [{ line: 0.5, p_over: 0.97 }] } }, 0.5);
    expect(low.source).toBe("rung_cal");
    expect(low.tail_note).toBe(TAIL_NOTE);
    expect(read(base, 5.5).tail_note).toBe(TAIL_NOTE);
    expect(read(base, 2.5).tail_note).toBeNull();
  });

  it("marks the model's line and says the quantile grid is rougher", () => {
    expect(read(base, 2.5).is_model_line).toBe(true);
    const q = read({ ...base, method: "quantile", rung_cal: null }, 1.5);
    expect(q.label).toContain(MODEL_ONLY);
    expect(q.label).toContain("rougher");
  });

  it("never prints an EV, a take-it word or hype", () => {
    const words = /\b(ev|expected value|take it|lock|guaranteed|bet this|best bet)\b/i;
    for (const l of [0.5, 1.5, 2.5, 3.5, 5.5]) {
      const r = read(base, l);
      expect(`${r.label} ${r.tail_note ?? ""}`).not.toMatch(words);
    }
    expect(`${MODEL_ONLY} ${TAIL_NOTE} ${calibratedLabel("v")}`).not.toMatch(words);
  });
});

describe("raw and calibrated side by side", () => {
  it("shows both, labelled, when the line is listed", () => {
    const r = read(base, 2.5);
    expect(r.p_raw).toBeCloseTo(pOver(PGE, 2.5), 12);
    expect(r.p_cal).toBe(0.6);
    expect(r.cal_note).toBeNull();
    const p = pairLines(r);
    expect(p.raw).toBe(`Model, raw: ${pct(pOver(PGE, 2.5))}`);
    expect(p.cal).toBe("Model, calibrated: 60% (adjusted for how past chances like this turned out; DraftKings line)");
  });
  it("shows raw alone with a reason when the line is not listed, never an approximation", () => {
    const r = read(base, 3.5);
    expect(r.p_cal).toBeNull();
    expect(pairLines(r).cal).toBeNull();
    expect(r.cal_note).toBe(r.p_raw < 0.5 ? CAL_OUT_OF_FIT : CAL_OUT_OF_RANGE);
    expect(r.p_raw).toBeCloseTo(pOver(PGE, 3.5), 12);
  });
  it("names the fit-support reason when raw is under 50% and the range reason when it is not", () => {
    const hi = read({ ...base, rung_cal: { ...RUNG, lines: [{ line: 0.5, p_over: 0.9 }] } }, 1.5);
    expect(hi.p_raw).toBeGreaterThanOrEqual(0.5);
    expect(hi.cal_note).toBe(CAL_OUT_OF_RANGE);
    const lo = read({ ...base, rung_cal: { ...RUNG, lines: [] } }, 5.5);
    expect(lo.p_raw).toBeLessThan(0.5);
    expect(lo.cal_note).toBe(CAL_OUT_OF_FIT);
  });
  it("with no rung_cal, gives the no-line reason only when there is no book line, else not-exported", () => {
    expect(read({ ...base, rung_cal: null, book_main_line: null }, 2.5).cal_note).toBe(CAL_NO_LINE);
    expect(read({ ...base, rung_cal: null }, 2.5).cal_note).toBe(CAL_NOT_EXPORTED);
    expect(read({ ...base, rung_cal: null }, 2.5).p_cal).toBeNull();
  });
  it("keeps a calibrated 0 and 1 and a raw edge value exactly", () => {
    const r = read({ ...base, rung_cal: { ...RUNG, lines: [{ line: 0.5, p_over: 1 }, { line: 9.5, p_over: 0 }] } }, 9.5);
    expect(r.p_cal).toBe(0);
    expect(pairLines(r).cal).toContain("0%");
    expect(read({ ...base, rung_cal: { ...RUNG, lines: [{ line: 0.5, p_over: 1 }] } }, 0.5).p_cal).toBe(1);
    expect(r.p_raw).toBe(0);
  });
  it("uses plain words with no advice terms", () => {
    const words = /\b(ev|expected value|take it|lock|guaranteed|bet this|best bet)\b/i;
    for (const t of [CAL_NO_LINE, CAL_OUT_OF_FIT, CAL_OUT_OF_RANGE, CAL_NOT_EXPORTED, RAW_LABEL, CAL_LABEL, CAL_WHY]) {
      expect(t).not.toMatch(words);
    }
  });
});

describe("slider geometry", () => {
  it("widens the range to hold the marks and never goes below half a point", () => {
    expect(sliderRange(0.5, 10.5, [null, 12.5])).toEqual({ min: 0.5, max: 12.5 });
    expect(sliderRange(2.5, 4.5, [0.5, 3.5])).toEqual({ min: 0.5, max: 4.5 });
    expect(sliderRange(-3, 4.5, [])).toEqual({ min: 0.5, max: 4.5 });
  });
  it("places marks in percent", () => {
    expect(percentAlong(5.5, 0.5, 10.5)).toBe(50);
    expect(percentAlong(99, 0.5, 10.5)).toBe(100);
    expect(percentAlong(1, 1, 1)).toBe(0);
  });
});
