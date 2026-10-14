import { describe, expect, it } from "vitest";
import { american, lineText, num, pct, pctBounded, tipTime } from "./format";
import { parse } from "./router";

describe("format", () => {
  it("formats probabilities and prices", () => {
    expect(pct(0.3649)).toBe("36%");
    expect(pct(0.3649, 1)).toBe("36.5%");
    expect(american(-110)).toBe("-110");
    expect(american(145)).toBe("+145");
    expect(lineText(24.5)).toBe("24.5");
    expect(lineText(24)).toBe("24");
    expect(num(26.04)).toBe("26");
    expect(num(26.6)).toBe("26.6");
  });

  it("does not print a tail the grid does not contain", () => {
    expect(pctBounded(0.95, true)).toBe("95%+");
    expect(pctBounded(0.05, true)).toBe("<5%");
    expect(pctBounded(0.62, false)).toBe("62%");
  });

  it("shows tip times in Eastern time", () => {
    expect(tipTime("2024-11-13T00:30:00Z")).toBe("7:30 PM ET");
  });
});

describe("router", () => {
  it("parses hashes", () => {
    expect(parse("")).toEqual({ name: "today", date: null });
    expect(parse("#/?date=2024-11-12")).toEqual({ name: "today", date: "2024-11-12" });
    expect(parse("#/game/0022400001")).toEqual({ name: "game", id: "0022400001" });
    expect(parse("#/swipe?date=2024-11-20")).toEqual({ name: "swipe", date: "2024-11-20" });
    expect(parse("#/picks")).toEqual({ name: "picks" });
    expect(parse("#/nonsense")).toEqual({ name: "today", date: null });
  });
});
