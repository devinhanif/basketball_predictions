import { describe, expect, it } from "vitest";
import type { LiveGameRow } from "./liveTypes";
import { decodeImport, encodeImport, gameForLeg, toLeg, validateImport, type ImportSpec } from "./importLink";

const GOOD: ImportSpec = {
  name: "Tonight ✓",
  contracts: 14.99,
  paid: 3.36,
  combo_price: 0.224,
  legs: [
    { label: "Paolo Banchero 45+ points", kind: "player", player: "Paolo Banchero", stat: "pts", threshold: 45, side: "over", ticker: "KXNBAPTS-26OCT21ATLORL-ORLPBANCHERO5-45" },
    { label: "Washington wins", kind: "win", team: "WAS" },
    { label: "Over 224.5", kind: "total", threshold: 224.5, side: "over" },
    { label: "DEN -5.5", kind: "spread", team: "DEN", threshold: -5.5 },
  ],
};

const row = (id: string, away: string, home: string): LiveGameRow => ({
  event_id: id,
  start_utc: "2024-11-12T23:00Z",
  et_date: "2024-11-12",
  status: { state: "in", period: 1, clock: "1:00", detail: "", completed: false },
  away: { side: "away", abbr: away, name: away, score: 0 },
  home: { side: "home", abbr: home, name: home, score: 0 },
});

describe("import link", () => {
  it("round-trips, including non-ASCII text", () => {
    const enc = encodeImport(GOOD);
    expect(enc).toMatch(/^[A-Za-z0-9_-]+$/);
    const back = decodeImport(enc);
    expect(back.ok && back.spec).toEqual(GOOD);
  });
  it("refuses garbage, wrong schema, unknown fields and oversized links", () => {
    expect(decodeImport("").ok).toBe(false);
    expect(decodeImport("not base64!").ok).toBe(false);
    expect(decodeImport("e30").ok).toBe(false); // {}
    expect(decodeImport("A".repeat(20_001)).ok).toBe(false);
    const enc = (o: unknown): string => encodeImport(o as ImportSpec);
    expect(decodeImport(enc({ ...GOOD, extra: 1 })).ok).toBe(false);
    expect(decodeImport(enc({ ...GOOD, contracts: 0 })).ok).toBe(false);
    expect(decodeImport(enc({ ...GOOD, legs: [] })).ok).toBe(false);
  });
  it.each([
    { legs: [{ label: "x", kind: "player", player: "A", stat: "blk", threshold: 5 }] },
    { legs: [{ label: "x", kind: "player", player: "A", stat: "pts" }] },
    { legs: [{ label: "x", kind: "win", team: "atl" }] },
    { legs: [{ label: "x", kind: "win", team: "ATL", side: "over" }] },
    { legs: [{ label: "x", kind: "win", team: "ATL", threshold: 1 }] },
    { legs: [{ label: "x", kind: "total", threshold: 200, ticker: "a b" }] },
    { legs: [{ label: "x", kind: "total", threshold: 200, entry_price: 3 }] },
    { legs: ["x"] },
    { paid: -1 },
    { combo_price: 2 },
  ])("rejects %j", (change) => {
    expect(validateImport({ ...GOOD, ...change }).ok).toBe(false);
  });
  it("defaults the name and side", () => {
    const r = validateImport({ contracts: 1, paid: 0.5, legs: [{ label: "x", kind: "total", threshold: 200 }] });
    expect(r.ok && r.spec.name).toBe("Imported parlay");
    expect(r.ok && r.spec.legs[0]?.side).toBe("over");
  });
});

describe("game and leg mapping", () => {
  const rows = [row("1", "ATL", "ORL"), row("2", "MIL", "WSH")];
  it("finds the game from the ticker's game code, with ESPN/Kalshi abbreviation differences", () => {
    expect(gameForLeg({ label: "", kind: "player", ticker: "KXNBAPTS-26OCT21MILWAS-MILKKUZMA18-10" }, rows)?.event_id).toBe("2");
    expect(gameForLeg({ label: "", kind: "player", ticker: "KXNBAPTS-26OCT21ATLORL-ORLPBANCHERO5-45" }, rows)?.event_id).toBe("1");
  });
  it("finds the game from the team, and gives up when unclear", () => {
    expect(gameForLeg({ label: "", kind: "win", team: "WAS" }, rows)?.event_id).toBe("2");
    expect(gameForLeg({ label: "", kind: "win", team: "DEN" }, rows)).toBeNull();
    expect(gameForLeg({ label: "", kind: "player", player: "A", stat: "pts", threshold: 5 }, rows)).toBeNull();
    expect(gameForLeg({ label: "", kind: "win", team: "ATL" }, [row("1", "ATL", "ORL"), row("3", "ATL", "BOS")])).toBeNull();
  });
  it("builds a tracker leg using the feed's team abbreviation", () => {
    const l = toLeg({ label: "WAS wins", kind: "win", team: "WAS" }, rows[1] ?? null, "id");
    expect(l).toMatchObject({ eventId: "2", team: "WSH", gameLabel: "MIL @ WSH", kind: "win" });
    const p = toLeg({ label: "x", kind: "player", player: "A B", stat: "reb", threshold: 10, side: "under", entry_price: 0.4 }, null, "id");
    expect(p).toMatchObject({ eventId: "", line: 10, stat: "reb", side: "under", entry: 0.4 });
  });
});
