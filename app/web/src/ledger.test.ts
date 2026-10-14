import { beforeEach, describe, expect, it } from "vitest";
import {
  addPick,
  canPlace,
  lastWeek,
  loadLedger,
  recordOf,
  settle,
  settleSide,
  streak,
  summarize,
  type Pick,
  type Settled,
} from "./ledger";
import { wilson } from "./stats";
import type { ResultRow } from "./types";

// Node has no localStorage; a tiny in-memory stand-in is enough for the ledger code.
class MemStore {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, v);
  }
  clear(): void {
    this.m.clear();
  }
}

const store = new MemStore();
(globalThis as unknown as { localStorage: MemStore }).localStorage = store;

const CLOCK = "2024-11-12T23:30:00+00:00";
const TIP = "2024-11-13T00:00:00Z";
const card = {
  prop_id: "g:1:pts",
  slate_date: "2024-11-12",
  game_id: "g",
  player: "A Player",
  team: "BOS",
  opponent: "ATL",
  stat: "pts" as const,
  line: 24.5,
  over_price: -110,
  under_price: -110,
  tip_utc: TIP,
};

describe("tip rule", () => {
  it("allows a pick before tip and refuses at or after it", () => {
    expect(canPlace(TIP, CLOCK)).toBe(true);
    expect(canPlace(TIP, "2024-11-13T00:00:00Z")).toBe(false);
    expect(canPlace(TIP, "2024-11-13T00:00:01Z")).toBe(false);
  });
});

describe("addPick", () => {
  beforeEach(() => {
    store.clear();
  });

  it("stamps made_at, the clock, the response time and a blind flag", () => {
    const now = new Date("2026-10-14T12:00:00Z");
    const { rows, result } = addPick([], card, "over", CLOCK, 1234.4, now);
    expect(result.ok).toBe(true);
    const p = rows[0];
    expect(p?.made_at).toBe(now.toISOString());
    expect(p?.slate_clock).toBe(CLOCK);
    expect(p?.response_ms).toBe(1234);
    expect(p?.saw_model).toBe(false);
    expect(p?.source).toBe("swipe");
    expect(loadLedger()).toHaveLength(1);
  });

  it("refuses after tip and stores nothing", () => {
    const { rows, result } = addPick([], card, "over", "2024-11-13T00:05:00Z", 10);
    expect(result).toEqual({ ok: false, reason: "after_tip" });
    expect(rows).toHaveLength(0);
    expect(loadLedger()).toHaveLength(0);
  });

  it("refuses a second answer to the same card", () => {
    const first = addPick([], card, "under", CLOCK, 10);
    const second = addPick(first.rows, card, "over", CLOCK, 10);
    expect(second.result).toEqual({ ok: false, reason: "duplicate" });
    expect(second.rows).toHaveLength(1);
    expect(second.rows[0]?.side).toBe("under");
  });

  it("keeps skips in the ledger", () => {
    const { rows } = addPick([], card, "skip", CLOCK, 10);
    expect(rows[0]?.side).toBe("skip");
  });
});

describe("settleSide", () => {
  it("settles over and under, with a push on a whole-number line", () => {
    expect(settleSide("over", 24.5, 25)).toBe("win");
    expect(settleSide("over", 24.5, 24)).toBe("loss");
    expect(settleSide("under", 24.5, 24)).toBe("win");
    expect(settleSide("under", 24.5, 25)).toBe("loss");
    expect(settleSide("over", 24, 24)).toBe("push");
    expect(settleSide("under", 24, 24)).toBe("push");
  });
});

function row(over: Partial<ResultRow>): ResultRow {
  return {
    prop_id: "g:1:pts",
    game_id: "g",
    player: "A Player",
    stat: "pts",
    line: 24.5,
    over_price: -110,
    under_price: -110,
    actual: 30,
    model_p_over: 0.6,
    model_p_under: 0.4,
    market_p_over: 0.5,
    market_p_under: 0.5,
    ...over,
  };
}

function pick(over: Partial<Pick>): Pick {
  return {
    ...card,
    id: card.prop_id,
    side: "over",
    made_at: "2026-10-14T12:00:00Z",
    slate_clock: CLOCK,
    response_ms: 1,
    source: "swipe",
    saw_model: false,
    ...over,
  };
}

describe("settle and summarize", () => {
  it("uses the model probability of the side taken, conditional on no push", () => {
    const { settled } = settle([pick({ side: "under" })], [row({ model_p_over: 0.5, model_p_under: 0.4 })]);
    expect(settled).toHaveLength(1);
    expect(settled[0]?.model_side).toBeCloseTo(0.4 / 0.9);
    expect(settled[0]?.outcome).toBe("loss");
  });

  it("leaves skips out and reports unmatched picks as open", () => {
    const { settled, open } = settle(
      [pick({ side: "skip" }), pick({ id: "x", prop_id: "x", side: "over" })],
      [row({})],
    );
    expect(settled).toHaveLength(0);
    expect(open).toHaveLength(1);
  });

  it("says 'not enough' below the minimum leg count", () => {
    const { settled } = settle([pick({})], [row({})]);
    const s = summarize([pick({})], settled);
    expect(s.decided).toBe(1);
    expect(s.enough).toBe(false);
    expect(s.bins.every((b) => !b.enough)).toBe(true);
  });

  it("gives an interval and bins once n reaches 20", () => {
    const picks: Pick[] = [];
    const rows: ResultRow[] = [];
    for (let i = 0; i < 20; i++) {
      picks.push(pick({ id: `p${i}`, prop_id: `p${i}` }));
      rows.push(row({ prop_id: `p${i}`, actual: i < 12 ? 30 : 10 }));
    }
    const { settled } = settle(picks, rows);
    const s = summarize(picks, settled);
    expect(s.wins).toBe(12);
    expect(s.hit_rate).toBeCloseTo(0.6);
    expect(s.enough).toBe(true);
    const bin = s.bins.find((b) => b.label === "60% and over");
    expect(bin?.n).toBe(20);
    expect(bin?.enough).toBe(true);
    expect(bin?.observed).toBeCloseTo(0.6);
  });

  it("excludes pushes from rates", () => {
    const { settled } = settle([pick({})], [row({ line: 30, actual: 30 })]);
    const s = summarize([pick({})], settled);
    expect(s.pushes).toBe(1);
    expect(s.decided).toBe(0);
    expect(s.hit_rate).toBeNull();
  });
});

describe("wilson", () => {
  it("matches known values", () => {
    const [lo, hi] = wilson(50, 100);
    expect(lo).toBeCloseTo(0.4038, 3);
    expect(hi).toBeCloseTo(0.5962, 3);
    expect(wilson(0, 0)).toEqual([0, 1]);
    const [l0, h0] = wilson(0, 10);
    expect(l0).toBe(0);
    expect(h0).toBeGreaterThan(0.2);
  });
});

describe("record and streak", () => {
  const mk = (id: string, outcome: "win" | "loss" | "push", madeAt: string): Settled => ({
    pick: { ...card, id, prop_id: id, side: "over", made_at: madeAt } as Pick,
    row: {} as ResultRow,
    outcome,
    model_side: 0.5,
    market_side: 0.5,
  });

  const rows = [
    mk("a", "loss", "2026-10-01T10:00:00Z"),
    mk("b", "win", "2026-10-12T10:00:00Z"),
    mk("c", "push", "2026-10-13T10:00:00Z"),
    mk("d", "win", "2026-10-13T11:00:00Z"),
    mk("e", "win", "2026-10-13T12:00:00Z"),
  ];

  it("counts wins and losses and leaves pushes out", () => {
    expect(recordOf(rows)).toEqual({ wins: 3, losses: 1 });
  });

  it("finds the current run; a push neither extends nor breaks it", () => {
    expect(streak(rows)).toEqual({ kind: "win", count: 3 });
    expect(streak([mk("x", "win", "2026-10-13T10:00:00Z"), mk("y", "loss", "2026-10-13T11:00:00Z")])).toEqual({
      kind: "loss",
      count: 1,
    });
    expect(streak([])).toBeNull();
  });

  it("keeps only picks from the last 7 days", () => {
    const week = lastWeek(rows, new Date("2026-10-14T12:00:00Z"));
    expect(week.map((s) => s.pick.id)).toEqual(["b", "c", "d", "e"]);
    expect(recordOf(week)).toEqual({ wins: 3, losses: 0 });
  });
});
