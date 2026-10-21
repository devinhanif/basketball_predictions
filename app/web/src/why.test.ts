import { describe, expect, it } from "vitest";
import { MAX_REASONS, parseReasons } from "./why";

describe("parseReasons", () => {
  it("is empty (panel hidden) when the field is absent or malformed", () => {
    expect(parseReasons(undefined)).toEqual([]);
    expect(parseReasons(null)).toEqual([]);
    expect(parseReasons("text")).toEqual([]);
    expect(parseReasons({})).toEqual([]);
    expect(parseReasons([])).toEqual([]);
  });
  it("keeps good reasons and drops empty or oversized text", () => {
    const r = parseReasons([
      { kind: "matchup", text: "  He guarded 31% of the opposing centre's possessions.  ", source: "player_game_matchups", as_of: "2024-11-11" },
      { kind: "weird", text: "Falls back to other." },
      { kind: "matchup", text: "" },
      { kind: "matchup", text: "x".repeat(401) },
      7,
    ]);
    expect(r.length).toBe(2);
    expect(r[0]?.text.startsWith("He guarded")).toBe(true);
    expect(r[0]?.source).toBe("player_game_matchups");
    expect(r[1]?.kind).toBe("other");
    expect(r[1]?.source).toBeNull();
  });
  it("caps the list", () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ kind: "other", text: `r${i.toString()}` }));
    expect(parseReasons(many).length).toBe(MAX_REASONS);
  });
});
