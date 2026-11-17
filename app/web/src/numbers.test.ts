import { describe, expect, it } from "vitest";
import { MINUS, MISSING, centsText, count, decimal, delta, deltaCents, nText, odds, percent } from "./format";
import { DEFAULTS, parseDensity, parseOdds } from "./prefs";

const bad = [null, undefined, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY];

describe("the one number formatter", () => {
  it("shows a missing number as a dash, never as zero", () => {
    for (const x of bad) {
      expect(percent(x)).toBe(MISSING);
      expect(decimal(x)).toBe(MISSING);
      expect(delta(x)).toBe(MISSING);
      expect(centsText(x)).toBe(MISSING);
      expect(deltaCents(x)).toBe(MISSING);
      expect(count(x)).toBe(MISSING);
      expect(odds(x)).toBe(MISSING);
    }
    expect(nText(null)).toBe(`n = ${MISSING}`);
  });

  it("never prints negative zero", () => {
    expect(percent(-0)).toBe("0%");
    expect(percent(-0.0001)).toBe("0%");
    expect(decimal(-0)).toBe("0");
    expect(decimal(-0.01)).toBe("0");
    expect(delta(-0)).toBe("0.0");
    expect(delta(-0.04)).toBe("0.0");
    expect(deltaCents(-0.01)).toBe("0c");
    expect(count(-0)).toBe("0");
    expect(centsText(-0)).toBe("0c");
  });

  it("uses a real minus sign", () => {
    expect(MINUS).toBe("−");
    expect(percent(-0.125, 1)).toBe(`${MINUS}12.5%`);
    expect(decimal(-1.5)).toBe(`${MINUS}1.5`);
    expect(delta(-4.66)).toBe(`${MINUS}4.7`);
    expect(delta(2, 1, " pts")).toBe("+2.0 pts");
    expect(deltaCents(-7.5)).toBe(`${MINUS}7.5c`);
    expect(deltaCents(3)).toBe("+3c");
    expect(count(-1234)).toBe(`${MINUS}1,234`);
  });

  it("formats percents, numbers, cents and counts", () => {
    expect(percent(0.3649)).toBe("36%");
    expect(percent(0.3649, 1)).toBe("36.5%");
    expect(percent(1)).toBe("100%");
    expect(decimal(26.04)).toBe("26");
    expect(decimal(26.6)).toBe("26.6");
    expect(decimal(3.14159, 2)).toBe("3.14");
    expect(centsText(51.4)).toBe("51.4c");
    expect(centsText(51)).toBe("51c");
    expect(count(12345.4)).toBe("12,345");
    expect(count(999)).toBe("999");
    expect(nText(1234)).toBe("n = 1,234");
  });

  it("writes a posted price as American or decimal odds", () => {
    expect(odds(-110)).toBe(`${MINUS}110`);
    expect(odds(145)).toBe("+145");
    expect(odds(-110, "decimal")).toBe("1.91");
    expect(odds(145, "decimal")).toBe("2.45");
    expect(odds(100, "decimal")).toBe("2.00");
    expect(odds(-100, "decimal")).toBe("2.00");
    expect(odds(0)).toBe(MISSING);
  });

  it("remembers the odds format and density with the app's current look as the default", () => {
    expect(DEFAULTS.odds).toBe("american");
    expect(DEFAULTS.density).toBe("comfortable");
    expect(parseOdds("decimal")).toBe("decimal");
    expect(parseOdds("junk")).toBe("american");
    expect(parseOdds(null)).toBe("american");
    expect(parseDensity("compact")).toBe("compact");
    expect(parseDensity("x")).toBe("comfortable");
  });
});
