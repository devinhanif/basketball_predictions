import { describe, expect, it } from "vitest";
import { chronological, defaultNight, todayET } from "./nights";

const d = (...dates: string[]) => dates.map((date) => ({ date }));

describe("F17: which night opens", () => {
  it("uses ET, not UTC, for today", () => {
    expect(todayET(new Date("2026-10-16T02:30:00Z"))).toBe("2026-10-15");
    expect(todayET(new Date("2026-10-16T12:00:00Z"))).toBe("2026-10-16");
  });
  it("opens today when held, even if the API lists newest first", () => {
    const now = new Date("2026-10-15T20:00:00Z");
    expect(defaultNight(d("2026-10-16", "2026-10-15"), now)).toBe("2026-10-15");
  });
  it("opens the next upcoming night when today is not held", () => {
    const now = new Date("2026-10-15T20:00:00Z");
    expect(defaultNight(d("2026-10-18", "2026-10-16", "2026-10-12"), now)).toBe("2026-10-16");
  });
  it("opens the most recent night when all are past, and null when none", () => {
    const now = new Date("2026-10-15T20:00:00Z");
    expect(defaultNight(d("2024-11-12", "2024-11-20"), now)).toBe("2024-11-20");
    expect(defaultNight([], now)).toBeNull();
  });
  it("sorts tabs oldest to newest", () => {
    expect(chronological(d("2026-10-16", "2026-10-15")).map((s) => s.date)).toEqual(["2026-10-15", "2026-10-16"]);
  });
});
