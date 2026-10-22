import { describe, expect, it } from "vitest";
import { cents, chancePct, monthProgress, signedUsd, usd } from "./bankroll";

describe("bankroll helpers", () => {
  it("formats money and signs", () => {
    expect(usd(49.44)).toBe("$49.44");
    expect(usd(-5)).toBe("-$5.00");
    expect(signedUsd(1.2)).toBe("+$1.20");
    expect(signedUsd(0)).toBe("$0.00");
  });
  it("formats prices and chances", () => {
    expect(cents(0.28)).toBe("28¢");
    expect(cents(0.077)).toBe("7.7¢");
    expect(chancePct(0.29)).toBe("29%");
  });
  it("places the balance on the month bar", () => {
    const p = monthProgress(49.44, 49.44, 49.93, 51.91);
    expect(p.fill).toBe(0);
    expect(p.delta).toBe(0);
    expect(p.targetAt).toBeCloseTo(0.2, 2);
    expect(monthProgress(60, 49.44, 49.93, 51.91).fill).toBe(1);
    expect(monthProgress(40, 49.44, 49.93, 51.91).fill).toBe(0);
  });
});
