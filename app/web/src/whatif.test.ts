import { describe, expect, it } from "vitest";
import {
  affineCounts,
  bookChance,
  feeFor,
  impliedFromAmerican,
  minutesWhatIf,
  momentsOf,
  overAt,
  parseOdds,
  priceRead,
  range80,
} from "./whatif";

// Y is 0..4 with probabilities .1 .2 .3 .25 .15: P(Y >= k) = 1, .9, .7, .4, .15
const PGE = [1, 0.9, 0.7, 0.4, 0.15, 0];

describe("moments", () => {
  it("reads the mean and spread of a count from P(Y >= k)", () => {
    const { mean, sd } = momentsOf(PGE);
    expect(mean).toBeCloseTo(2.15, 12);
    expect(sd).toBeCloseTo(Math.sqrt(1.4275), 12); // E[Y^2] = 6.05, mean 2.15
  });
});

describe("affineCounts (the minutes what-if)", () => {
  it("is the identity when nothing moves", () => {
    const out = affineCounts(PGE, 1, 0);
    for (let k = 0; k < PGE.length; k++) expect(out[k]).toBeCloseTo(PGE[k] ?? 0, 12);
  });
  it("never rises with the count, starts at one and ends at zero", () => {
    for (const s of [0.4, 0.75, 1, 1.3, 2.2]) {
      const out = affineCounts(PGE, s, 0);
      expect(out[0]).toBe(1);
      expect(out[out.length - 1]).toBe(0);
      for (let k = 1; k < out.length; k++) expect(out[k] as number).toBeLessThanOrEqual(out[k - 1] as number);
    }
  });
  it("moves the mean about as far as the minutes move", () => {
    const base = momentsOf(PGE).mean;
    for (const s of [0.5, 0.8, 1.25, 1.5]) {
      const m = momentsOf(affineCounts(PGE, s, 0)).mean;
      expect(Math.abs(m - s * base)).toBeLessThan(0.35); // whole-count rounding of a scaled count
    }
  });
  it("doubling minutes: over 3.5 becomes over 1.5 of the original", () => {
    const out = affineCounts(PGE, 2, 0);
    expect(overAt(out, 3.5)).toBeCloseTo(overAt(PGE, 1.5), 12);
    expect(overAt(out, 7.5)).toBeCloseTo(overAt(PGE, 3.5), 12);
  });
  it("zero minutes means a zero", () => {
    expect(affineCounts(PGE, 0, 0)).toEqual([1, 0]);
    expect(overAt(minutesWhatIf(PGE, 30, 0, null), 0.5)).toBe(0);
  });
  it("more minutes, more over; fewer minutes, less", () => {
    expect(overAt(minutesWhatIf(PGE, 30, 45, null), 2.5)).toBeGreaterThan(overAt(PGE, 2.5));
    expect(overAt(minutesWhatIf(PGE, 30, 15, null), 2.5)).toBeLessThan(overAt(PGE, 2.5));
    expect(overAt(minutesWhatIf(PGE, 30, 30, null), 2.5)).toBeCloseTo(overAt(PGE, 2.5), 12);
  });
  it("narrowing keeps the centre and shrinks the spread", () => {
    const { mean } = momentsOf(PGE);
    const wide = minutesWhatIf(PGE, 30, 30, null);
    const known = minutesWhatIf(PGE, 30, 30, { share: 0.5, mean });
    expect(momentsOf(known).sd).toBeLessThan(momentsOf(wide).sd);
    expect(Math.abs(momentsOf(known).mean - mean)).toBeLessThan(0.3);
  });
  it("a zero minutes share changes nothing", () => {
    const { mean } = momentsOf(PGE);
    const a = minutesWhatIf(PGE, 30, 33, null);
    const b = minutesWhatIf(PGE, 30, 33, { share: 0, mean });
    expect(b.length).toBe(a.length);
    for (let k = 0; k < a.length; k++) expect(b[k]).toBeCloseTo(a[k] ?? 0, 9);
  });
});

describe("range80", () => {
  it("matches the strip's middle 80 percent", () => {
    expect(range80(PGE)).toEqual([0, 4]);
  });
});

describe("price and fees", () => {
  it("turns American odds into the chance they imply", () => {
    expect(impliedFromAmerican(-110)).toBeCloseTo(110 / 210, 12);
    expect(impliedFromAmerican(150)).toBeCloseTo(0.4, 12);
    expect(impliedFromAmerican(100)).toBeCloseTo(0.5, 12);
    expect(impliedFromAmerican(50)).toBeNull();
    expect(impliedFromAmerican(Number.NaN)).toBeNull();
  });
  it("parses what a visitor types", () => {
    expect(parseOdds("-110")).toBe(-110);
    expect(parseOdds("+150")).toBe(150);
    expect(parseOdds(" 150 ")).toBe(150);
    expect(parseOdds("1.5")).toBeNull();
    expect(parseOdds("")).toBeNull();
    expect(parseOdds("-90")).toBeNull();
    expect(parseOdds("abc")).toBeNull();
  });
  it("with no fee, EV per $1 is q / implied - 1 and is zero when the chances match", () => {
    const r = priceRead(0.5, 100, "none", null, 0);
    expect(r?.ev).toBeCloseTo(0, 12);
    const s = priceRead(0.55, -110, "none", null, 0);
    expect(s?.ev).toBeCloseTo(0.55 / (110 / 210) - 1, 12);
    expect(s?.breakEven).toBeCloseTo(110 / 210, 12);
  });
  it("a fee raises the break-even chance and lowers EV", () => {
    const free = priceRead(0.6, 100, "none", null, 0);
    const fee = priceRead(0.6, 100, "custom", null, 2);
    expect(fee?.breakEven).toBeCloseTo(0.52, 12);
    expect((fee?.ev ?? 0) < (free?.ev ?? 0)).toBe(true);
    expect(fee?.gap).toBeCloseTo(0.08, 12);
  });
  it("the stored schedule is coefficient * c * (1 - c)", () => {
    expect(feeFor("schedule", 0.5, 0.07, 0)).toBeCloseTo(0.0175, 12);
    expect(feeFor("schedule", 0.5, null, 0)).toBe(0);
    expect(feeFor("custom", 0.5, 0.07, -3)).toBe(0);
    expect(feeFor("none", 0.5, 0.07, 9)).toBe(0);
  });
  it("a price that is not a price reads as nothing", () => {
    expect(priceRead(0.5, 20, "none", null, 0)).toBeNull();
  });
  it("removes the book's margin from a two-sided quote", () => {
    expect(bookChance(-110, -110)).toBeCloseTo(0.5, 12);
    expect(bookChance(-110, 20)).toBeNull();
  });
});
