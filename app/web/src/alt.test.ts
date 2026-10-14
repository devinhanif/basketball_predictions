import { describe, expect, it } from "vitest";
import { altRange } from "./AltLine";

describe("altRange", () => {
  it("stays on the half-point grid around the posted line", () => {
    expect(altRange("pts", 26.5)).toEqual({ min: 18.5, max: 34.5 });
    expect(altRange("fg3m", 2.5)).toEqual({ min: 0.5, max: 5.5 });
  });

  it("never goes below half a point", () => {
    expect(altRange("ast", 1.5).min).toBe(0.5);
  });
});
