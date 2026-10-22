import { describe, expect, it } from "vitest";
import type { BoxPlayer, LiveGame, LiveStatus } from "./liveTypes";
import {
  STORE_KEY,
  comboCheck,
  exitSignal,
  fullHedgeCount,
  hedge,
  legStatus,
  loadParlays,
  mid,
  normName,
  saveParlays,
  summarize,
  takerFee,
  withMarket,
  type Leg,
  type LegPrice,
  type Parlay,
  type StoreLike,
} from "./tracker";

const st = (state: "pre" | "in" | "post"): LiveStatus => ({ state, period: 3, clock: "3:32", detail: "3:32 - 3rd", completed: state === "post" });
const player = (name: string, pts: number | null, extra: Partial<BoxPlayer> = {}): BoxPlayer => ({
  player_id: "1", name, team: "ATL", starter: true, dnp: false, min: "21", pts, reb: 3, ast: 6, fg3m: 0, pf: 2, pm: "+3", forecast: null, ...extra,
});
const game = (state: "pre" | "in" | "post", home: number, away: number, players: BoxPlayer[] = []): LiveGame => ({
  event_id: "1", source: "ESPN unofficial feed", fetched_at: "t", stale: false, age_s: 0, status: st(state),
  home: { side: "home", abbr: "ORL", name: "Orlando Magic", score: home },
  away: { side: "away", abbr: "ATL", name: "Atlanta Hawks", score: away },
  players, box_available: players.length > 0, forecast_date: null, forecast_note: null,
});
const leg = (o: Partial<Leg>): Leg => ({
  id: "a", kind: "player", eventId: "1", gameLabel: "ATL @ ORL", startUtc: "2024-11-12T23:00Z", player: "Jalen Johnson",
  stat: "pts", side: "over", line: 15, team: "ATL", ticker: "", entry: null, ...o,
});

describe("leg status", () => {
  it("player over: done when the count reaches the need, even mid-game", () => {
    expect(legStatus(leg({}), game("in", 0, 0, [player("Jalen Johnson", 15)])).state).toBe("done");
    expect(legStatus(leg({}), game("in", 0, 0, [player("Jalen Johnson", 14)])).state).toBe("live");
  });
  it("player over: dead only once the game is final", () => {
    expect(legStatus(leg({}), game("post", 0, 0, [player("Jalen Johnson", 14)])).state).toBe("dead");
  });
  it("player under: dead on reaching the line, done at the final", () => {
    const u = leg({ side: "under", line: 15 });
    expect(legStatus(u, game("in", 0, 0, [player("Jalen Johnson", 15)])).state).toBe("dead");
    expect(legStatus(u, game("post", 0, 0, [player("Jalen Johnson", 9)])).state).toBe("done");
  });
  it("matches names across accents and suffixes; says unknown for a missing player once final", () => {
    expect(normName("Jaren Jackson Jr.")).toBe("jaren jackson");
    expect(normName("Nikola Jokić")).toBe("nikola jokic");
    expect(legStatus(leg({ player: "Nobody" }), game("post", 0, 0, [player("A B", 5)])).state).toBe("unknown");
    expect(legStatus(leg({ player: "Nobody" }), game("in", 0, 0, [player("A B", 5)])).state).toBe("live");
  });
  it("pre-game is pre, and a player who did not play is unknown at the final", () => {
    expect(legStatus(leg({}), game("pre", 0, 0)).state).toBe("pre");
    expect(legStatus(leg({}), game("post", 0, 0, [player("Jalen Johnson", null, { dnp: true })])).state).toBe("unknown");
  });
  it("team win and spread resolve at the final", () => {
    const win = leg({ kind: "win", team: "ATL" });
    expect(legStatus(win, game("in", 89, 91)).state).toBe("live");
    expect(legStatus(win, game("post", 89, 91)).state).toBe("done");
    expect(legStatus(win, game("post", 92, 91)).state).toBe("dead");
    const sp = leg({ kind: "spread", team: "ATL", line: -3.5 }); // ATL must win by 4+
    expect(legStatus(sp, game("post", 80, 84)).state).toBe("done");
    expect(legStatus(sp, game("post", 80, 83)).state).toBe("dead");
    expect(legStatus(leg({ kind: "spread", team: "ORL", line: 2.5 }), game("post", 80, 82)).state).toBe("done");
  });
  it("total over is done as soon as it passes the line; under is dead then", () => {
    const o = leg({ kind: "total", side: "over", line: 170.5 });
    const u = leg({ kind: "total", side: "under", line: 170.5 });
    expect(legStatus(o, game("in", 89, 91)).state).toBe("done");
    expect(legStatus(u, game("in", 89, 91)).state).toBe("dead");
    expect(legStatus(o, game("in", 50, 51)).state).toBe("live");
    expect(legStatus(u, game("post", 50, 51)).state).toBe("done");
  });
  it("a game missing from the feed is unknown, not a guess", () => {
    expect(legStatus(leg({}), null).state).toBe("unknown");
  });
});

const P = (bid: number, ask: number): LegPrice => ({ yesBid: bid, yesAsk: ask, noBid: 1 - ask, noAsk: 1 - bid, last: null });

describe("parlay value", () => {
  const two: Parlay = { id: "p", name: "t", legs: [leg({ id: "a", ticker: "A" }), leg({ id: "b", ticker: "B", player: "X Y" })], shares: 10, totalCost: 2, comboPrice: null };
  const statuses: Record<string, "live" | "done" | "dead"> = { a: "live", b: "live" };
  const prices: Record<string, LegPrice> = { a: P(0.7, 0.8), b: P(0.4, 0.5) };
  const run = () =>
    summarize(
      two,
      (l) => ({ state: statuses[l.id] ?? "live", progress: "", note: null }),
      (l) => prices[l.id] ?? null,
    );

  it("fair value is shares times the product of mids, and compares with what was paid", () => {
    const s = run();
    expect(s.jointP).toBeCloseTo(0.75 * 0.45, 10);
    expect(s.fairValue).toBeCloseTo(10 * 0.75 * 0.45, 10);
    expect(s.paid).toBe(2);
    expect(s.gainNow).toBeCloseTo(3.375 - 2, 10);
    expect(s.riskLeg?.leg.id).toBe("b");
  });
  it("a hit leg counts 1, a dead leg zeroes the parlay, and a missing price blocks the product", () => {
    statuses["a"] = "done";
    expect(run().jointP).toBeCloseTo(0.45, 10);
    statuses["b"] = "dead";
    expect(run().state).toBe("dead");
    expect(run().fairValue).toBe(0);
    statuses["a"] = "live";
    statuses["b"] = "live";
    delete prices["b"];
    const s = run();
    expect(s.fairValue).toBeNull();
    expect(s.unpriced).toHaveLength(1);
  });
  it("paid falls back to combo price times shares", () => {
    const s = summarize({ ...two, totalCost: null, comboPrice: 0.3 }, () => ({ state: "live", progress: "", note: null }), () => P(0.5, 0.5));
    expect(s.paid).toBeCloseTo(3, 10);
  });
  it("mid uses bid and ask, else the last trade", () => {
    expect(mid(P(0.7, 0.8))).toBeCloseTo(0.75, 10);
    expect(mid({ yesBid: null, yesAsk: null, noBid: null, noAsk: null, last: 0.4 })).toBe(0.4);
    expect(mid(null)).toBeNull();
  });
});

describe("hedge", () => {
  it("the full hedge equalises the two worlds exactly, fee included", () => {
    const shares = 10;
    const h = hedge({ shares, paid: 2, noAsk: 0.3, noMid: 0.28, count: fullHedgeCount(shares) });
    expect(h.netAllHit).toBeCloseTo(h.netLegMisses, 10);
    expect(h.cost).toBeCloseTo(3, 10);
    expect(h.netAllHit).toBeCloseTo(10 - 2 - 3 - takerFee(10, 0.3), 10);
    expect(h.spread).toBeCloseTo(0.2, 10);
  });
  it("hedging nothing leaves the bet as it was; the third world loses the premium", () => {
    const h = hedge({ shares: 10, paid: 2, noAsk: 0.3, noMid: 0.3, count: 0 });
    expect(h.netAllHit).toBe(8);
    expect(h.netLegMisses).toBe(-2);
    const f = hedge({ shares: 10, paid: 2, noAsk: 0.3, noMid: 0.3, count: 10 });
    expect(f.netOtherMisses).toBeCloseTo(-2 - 3 - f.fee, 10);
  });
  it("a hedge never improves the expected value at the quoted ask", () => {
    // fair p(hit)=0.7, No ask 0.32 (fair No is 0.30): expected net with hedge <= without
    const p = 0.7;
    const ev = (count: number) => {
      const h = hedge({ shares: 10, paid: 2, noAsk: 0.32, noMid: 0.3, count });
      return p * h.netAllHit + (1 - p) * h.netLegMisses;
    };
    expect(ev(10)).toBeLessThan(ev(0));
    expect(ev(5)).toBeLessThan(ev(0));
  });
  it("fee: 7% x p x (1-p), rounded up to the cent, zero at the ends", () => {
    expect(takerFee(1, 0.5)).toBe(0.02);
    expect(takerFee(100, 0.5)).toBe(1.75);
    expect(takerFee(10, 0)).toBe(0);
    expect(takerFee(10, 1)).toBe(0);
  });
});

describe("combo vs legs", () => {
  const rows = (asks: number[]) =>
    summarize(
      { id: "p", name: "", legs: asks.map((_, i) => leg({ id: String(i) })), shares: 1, totalCost: null, comboPrice: null },
      () => ({ state: "live", progress: "", note: null }),
      (l) => P(0.3, asks[Number(l.id)] ?? 0.5),
    ).rows;
  it("flags a combo only when it is below the product of the legs' asks", () => {
    const r = rows([0.8, 0.5]);
    expect(comboCheck(0.3, r).below).toBe(true);
    expect(comboCheck(0.3, r).gapVsAsk).toBeCloseTo(0.1, 10);
    expect(comboCheck(0.45, r).below).toBe(false);
    expect(comboCheck(0.4, r).below).toBe(false);
  });
});

describe("persistence", () => {
  const mem = (): StoreLike & { m: Map<string, string> } => {
    const m = new Map<string, string>();
    return { m, getItem: (k) => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); } };
  };
  const p: Parlay = { id: "p", name: "n", legs: [leg({})], shares: 2, totalCost: 1, comboPrice: null };
  it("round-trips and ignores garbage", () => {
    const s = mem();
    expect(saveParlays(s, [p])).toBe(true);
    expect(loadParlays(s)).toEqual([p]);
    s.m.set(STORE_KEY, "{not json");
    expect(loadParlays(s)).toEqual([]);
    s.m.set(STORE_KEY, JSON.stringify([{ id: 1 }, p]));
    expect(loadParlays(s)).toEqual([p]);
  });
  it("blocked storage never throws", () => {
    const bad: StoreLike = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(loadParlays(bad)).toEqual([]);
    expect(saveParlays(bad, [p])).toBe(false);
    expect(loadParlays(null)).toEqual([]);
    expect(saveParlays(null, [p])).toBe(false);
  });
});

describe("settled market", () => {
  it("a settled Kalshi result overrides the feed", () => {
    const s = { state: "live" as const, progress: "x", note: null };
    const m = { ticker: "T", title: "", status: "finalized", result: "yes", yes_bid: null, yes_ask: null, no_bid: null, no_ask: null, last: null, found: true };
    expect(withMarket(s, m).state).toBe("done");
    expect(withMarket(s, { ...m, result: "no" }).state).toBe("dead");
    expect(withMarket(s, { ...m, result: null }).state).toBe("live");
    expect(withMarket(s, undefined).state).toBe("live");
  });
});

describe("exit signal", () => {
  const par: Parlay = { id: "p", name: "", legs: [leg({ id: "a" }), leg({ id: "b", player: "X Y" })], shares: 10, totalCost: 2, comboPrice: null };
  const rowsFor = (sa: "live" | "done" | "dead", bidA = 0.6, askA = 0.7) =>
    summarize(
      par,
      (l) => ({ state: l.id === "a" ? sa : "live", progress: "", note: null }),
      (l) => (l.id === "a" ? P(bidA, askA) : P(0.4, 0.5)),
    ).rows;
  it("entry edge is model value at entry minus what was paid, with every source named", () => {
    const e = exitSignal(par, rowsFor("live"), { a: { p: 0.8, source: "m", reason: null }, b: { p: 0.5, source: "m", reason: null } });
    expect(e.entryModelValue).toBeCloseTo(4, 10);
    expect(e.entryEdge).toBeCloseTo(2, 10);
    expect(e.legs[0]?.entrySource).toBe("m");
    expect(e.legs[0]?.nowSource).toContain("no in-game model");
  });
  it("a settled leg is 1 or 0; a live leg is the market mid unless overridden", () => {
    const m = { a: { p: 0.8, source: "m", reason: null }, b: { p: 0.5, source: "m", reason: null } };
    expect(exitSignal(par, rowsFor("done"), m).modelNow).toBeCloseTo(10 * 0.45, 10);
    const rows = rowsFor("done").map((r) => (r.leg.id === "b" ? { ...r, leg: { ...r.leg, pUser: 0.9 } } : r));
    const o = exitSignal(par, rows, m);
    expect(o.legs[1]?.nowP).toBe(0.9);
    expect(o.legs[1]?.nowSource).toBe("your number");
  });
  it("signal: edge gone when the offer reaches model value, remains when below; spread cost shown", () => {
    const m = { a: { p: 0.8, source: "m", reason: null }, b: { p: 0.5, source: "m", reason: null } };
    // legs sold at bids: 0.6 x 0.4 x 10 = 2.4; model now = mids 0.65 x 0.45 x 10 = 2.925
    const e = exitSignal(par, rowsFor("live"), m);
    expect(e.offer).toBeCloseTo(2.4, 10);
    expect(e.verdict).toBe("remains");
    expect(e.spreadCost).toBeCloseTo(2.925 - 2.4, 10);
    expect(exitSignal({ ...par, exitOffer: 0.3 }, rowsFor("live"), m).verdict).toBe("gone");
  });
  it("unknown without a probability, settled when a leg is dead", () => {
    expect(exitSignal(par, rowsFor("live"), {}).entryEdge).toBeNull();
    expect(exitSignal(par, rowsFor("live"), {}).verdict).toBe("unknown");
    expect(exitSignal(par, rowsFor("live"), { a: { p: 0.8, source: "m", reason: null }, b: { p: 0.5, source: "m", reason: null } }).marketOnly).toBe(true);
    expect(exitSignal(par, rowsFor("dead"), {}).verdict).toBe("settled");
  });
});
