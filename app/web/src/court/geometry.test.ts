import { describe, expect, it } from "vitest";
import data from "../fixtures/studio_shots.json";
import {
  NO_FILTER,
  ZONES,
  applyFilters,
  clockText,
  nbaGameUrl,
  nearestShot,
  orderShots,
  periodLabel,
  playersOf,
  toCourt,
  zoneShade,
  zoneStats,
  type ShotRow,
} from "./geometry";

const shots = data.shots as ShotRow[];
const one = (over: Partial<ShotRow>): ShotRow => ({ ...(shots[0] as ShotRow), ...over });

describe("studio fixture", () => {
  it("is real 2024-25 data: every game is before 2025-07 and shots sit on the half court", () => {
    for (const g of data.games) expect(g.game_date < "2025-07-01").toBe(true);
    expect(shots.length).toBe(data.meta.n_shots);
    for (const s of shots) {
      expect(Math.abs(s.loc_x)).toBeLessThanOrEqual(250);
      expect(s.loc_y).toBeGreaterThanOrEqual(-53);
      expect(ZONES.includes(s.zone as never) || s.zone === "Backcourt").toBe(true);
    }
  });
  it("has every shot in a listed game", () => {
    const ids = new Set(data.games.map((g) => g.game_id));
    for (const s of shots) expect(ids.has(s.game_id)).toBe(true);
  });
});

describe("filters and order", () => {
  const g = shots.filter((s) => s.game_id === data.games[0]?.game_id);
  it("orders by period then clock counting down", () => {
    const o = orderShots(g);
    for (let i = 1; i < o.length; i++) {
      const a = o[i - 1] as ShotRow;
      const b = o[i] as ShotRow;
      expect(a.period < b.period || (a.period === b.period && a.clock_seconds >= b.clock_seconds)).toBe(true);
    }
  });
  it("splits made and missed without losing a shot", () => {
    const m = applyFilters(g, { ...NO_FILTER, result: "made" }).length;
    const x = applyFilters(g, { ...NO_FILTER, result: "missed" }).length;
    expect(m + x).toBe(g.length);
  });
  it("splits 2s and 3s", () => {
    const a = applyFilters(g, { ...NO_FILTER, type: "3PT" }).length;
    const b = applyFilters(g, { ...NO_FILTER, type: "2PT" }).length;
    expect(a + b).toBe(g.length);
  });
  it("can ignore the result filter for zone percentages", () => {
    const only = { ...NO_FILTER, result: "made" as const };
    expect(applyFilters(g, only, true).length).toBe(g.length);
  });
  it("filters by player", () => {
    const p = playersOf(g)[0];
    expect(p).toBeTruthy();
    expect(applyFilters(g, { ...NO_FILTER, playerId: p?.id ?? 0 }).length).toBe(p?.n);
  });
});

describe("zones", () => {
  it("counts every zoned shot once", () => {
    const g = shots.filter((s) => s.zone !== "Backcourt");
    expect(zoneStats(g).reduce((n, z) => n + z.att, 0)).toBe(g.length);
  });
  it("does not shade a thin zone", () => {
    expect(zoneShade({ zone: "Mid-Range", att: 4, made: 4, pct: 1 })).toBe(0);
    expect(zoneShade({ zone: "Mid-Range", att: 5, made: 4, pct: 0.8 })).toBeGreaterThan(0.12);
    expect(zoneShade({ zone: "Mid-Range", att: 0, made: 0, pct: null })).toBe(0);
  });
});

describe("helpers", () => {
  it("formats the clock and period", () => {
    expect(clockText(703)).toBe("11:43");
    expect(clockText(65)).toBe("1:05");
    expect(periodLabel(2)).toBe("Q2");
    expect(periodLabel(5)).toBe("OT1");
  });
  it("finds the nearest shot inside the tap radius only", () => {
    const s = [one({ loc_x: 0, loc_y: 100 }), one({ loc_x: 200, loc_y: 100 })];
    expect(nearestShot(s, 10, 105)).toBe(0);
    expect(nearestShot(s, 100, 100)).toBe(-1);
  });
  it("maps the picture to the court: bottom centre is the basket end", () => {
    const [x, y] = toCourt(100, 470 * 0.5 + 0, 200, 470);
    expect(x).toBe(0);
    expect(Math.round(y)).toBe(Math.round(365 - (-52.5 + 235)));
  });
  it("links out to the official game page only", () => {
    expect(nbaGameUrl("0022400001")).toBe("https://www.nba.com/game/0022400001");
  });
});
