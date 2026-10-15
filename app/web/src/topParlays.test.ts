import { describe, expect, it } from "vitest";
import { etDate, evText, hasTickets, isKeepYourMoney, isStale, madeAfterTip, type TopParlays } from "./topParlays";

describe("top parlays helpers", () => {
  it("stale when made before the slate's Eastern day", () => {
    expect(isStale("2024-11-11T22:10:00Z", "2024-11-12")).toBe(true);
    expect(isStale("2024-11-12T22:10:00Z", "2024-11-12")).toBe(false);
    // 01:00Z on the 20th is 20:00 EDT on the 19th: made the evening before, so stale.
    expect(isStale("2026-10-20T01:00Z", "2026-10-20")).toBe(true);
    expect(isStale("2026-10-20T04:30Z", "2026-10-20")).toBe(false);
    expect(etDate("2026-10-20T03:59:59Z")).toBe("2026-10-19");
    expect(etDate("2026-10-20T04:00:00Z")).toBe("2026-10-20");
  });
  it("detects a file made after the first tip", () => {
    const leg = { tipoff: "2024-01-02T00:00:00Z" };
    const mk = (made_at: string) =>
      ({ made_at, sizes: { "2": [{ legs: [leg, leg] }], "3": [] } }) as unknown as TopParlays;
    expect(madeAfterTip(mk("2024-01-01T22:10:00Z"))).toBe(false);
    expect(madeAfterTip(mk("2024-01-02T00:00:00Z"))).toBe(true);
    expect(madeAfterTip(mk("2024-01-05T00:00:00Z"))).toBe(true);
    expect(madeAfterTip({ made_at: "2024-01-05T00:00:00Z", sizes: { "2": [], "3": [] } } as unknown as TopParlays)).toBe(false);
  });
  it("signs EV per dollar", () => {
    expect(evText(0.046)).toBe("+$0.05");
    expect(evText(-0.24)).toBe("-$0.24");
    expect(evText(0)).toBe("+$0.00");
    expect(evText(-0.004)).toBe("+$0.00");
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
