import { describe, expect, it } from "vitest";
import { MISS_TTL_MS, clearMiss, missedRecently, noteMiss } from "./missMemo";

function mem() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
}

describe("remembered absences", () => {
  it("remembers a miss for the TTL only, and forgets it when the file is found", () => {
    const s = mem();
    expect(missedRecently("desk", 1000, s)).toBe(false);
    noteMiss("desk", 1000, s);
    expect(missedRecently("desk", 1000 + MISS_TTL_MS - 1, s)).toBe(true);
    expect(missedRecently("desk", 1000 + MISS_TTL_MS, s)).toBe(false);
    clearMiss("desk", s);
    expect(missedRecently("desk", 1001, s)).toBe(false);
  });
  it("a clock that went backwards, junk, or no storage never claims a miss", () => {
    const s = mem();
    noteMiss("k", 5000, s);
    expect(missedRecently("k", 4000, s)).toBe(false);
    s.setItem("ph.miss.j", "junk");
    expect(missedRecently("j", 5000, s)).toBe(false);
    expect(missedRecently("k", 5000, null)).toBe(false);
    expect(() => { noteMiss("k", 1, null); }).not.toThrow();
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
    expect(missedRecently("k", 1, broken)).toBe(false);
    expect(() => { noteMiss("k", 1, broken); clearMiss("k", broken); }).not.toThrow();
  });
});
