import { describe, expect, it } from "vitest";
import { evText, hasTickets, isKeepYourMoney, isStale, type TopParlays } from "./topParlays";

describe("top parlays helpers", () => {
  it("stale when made before the slate day", () => {
    expect(isStale("2024-11-11T22:10:00Z", "2024-11-12")).toBe(true);
    expect(isStale("2024-11-12T22:10:00Z", "2024-11-12")).toBe(false);
  });
  it("signs EV per dollar", () => {
    expect(evText(0.046)).toBe("+$0.05");
    expect(evText(-0.24)).toBe("-$0.24");
    expect(evText(0)).toBe("+$0.00");
  });
  it("keep your money is the only verdict called that", () => {
    expect(isKeepYourMoney("keep_your_money")).toBe(true);
    expect(isKeepYourMoney("anything_else")).toBe(false);
  });
  it("detects an empty slate", () => {
    const d = { sizes: { "2": [], "3": [] } } as unknown as TopParlays;
    expect(hasTickets(d)).toBe(false);
  });
});
