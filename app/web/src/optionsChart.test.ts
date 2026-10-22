import { describe, expect, it } from "vitest";
import { comboSeries, legDeltas, payoffRows, type HistPoint } from "./optionsChart";
import { holdings, parseCsv, parseKalshiCsv } from "./csvImport";

const pt = (t: string, bid: number, ask: number): HistPoint => ({ t, yes_bid: bid, yes_ask: ask, src: "capture" });
const T = (m: number) => `2024-11-12T18:0${m.toString()}:00Z`;

describe("combo value over time", () => {
  const hist = {
    A: [pt(T(0), 0.6, 0.8), pt(T(2), 0.8, 1), pt(T(4), 0.98, 1)],
    B: [pt(T(1), 0.4, 0.6), pt(T(3), 0.2, 0.4)],
  };
  it("is shares x product of mids with the last price carried forward, starting when every leg has one", () => {
    const s = comboSeries(["A", "B"], hist, 10);
    expect(s.points.map((p) => p.t)).toEqual([T(1), T(2), T(3), T(4)].map(Date.parse));
    expect(s.points[0]?.value).toBeCloseTo(10 * 0.7 * 0.5, 10);
    expect(s.points[1]?.value).toBeCloseTo(10 * 0.9 * 0.5, 10);
    expect(s.points[2]?.value).toBeCloseTo(10 * 0.9 * 0.3, 10);
    expect(s.points[3]?.value).toBeCloseTo(10 * 0.99 * 0.3, 10);
  });
  it("marks a leg settled where its price reaches 97c and stays", () => {
    const s = comboSeries(["A", "B"], hist, 10);
    expect(s.marks).toEqual([{ leg: 0, t: Date.parse(T(4)), kind: "hit" }]);
  });
  it("says which legs are missing rather than guessing", () => {
    const s = comboSeries(["A", "", "C"], hist, 10);
    expect(s.points).toEqual([]);
    expect(s.missing).toEqual([1, 2]);
  });
});

describe("deltas and payoff", () => {
  it("delta is shares x product of the other legs per cent, sorted, and equals a real 1-cent move", () => {
    const v = [0.9, 0.5, 0.2];
    const d = legDeltas(10, v);
    expect(d.map((x) => x.leg)).toEqual([2, 1, 0]);
    const value = (x: number[]) => 10 * x.reduce((a, b) => a * b, 1);
    for (const e of d) {
      const bumped = v.map((p, i) => (i === e.leg ? p + 0.01 : p));
      expect(value(bumped) - value(v)).toBeCloseTo(e.perCent, 10);
    }
  });
  it("skips settled legs; a leg is skipped when a neighbour is unpriced", () => {
    expect(legDeltas(10, [1, 0.5], new Set([0])).map((x) => x.leg)).toEqual([1]);
    expect(legDeltas(10, [null, 0.5]).map((x) => x.leg)).toEqual([0]);
  });
  it("payoff: if a leg hits the combo is shares x the others, if it misses it is zero", () => {
    const r = payoffRows(10, [0.9, 0.5]);
    expect(r).toEqual([
      { leg: 0, ifHit: 5, ifMiss: 0 },
      { leg: 1, ifHit: 9, ifMiss: 0 },
    ]);
  });
});

describe("CSV import", () => {
  const csv = 'Ticker,Side,Action,Contracts,Price,Created,Fee,Notes\nKX-A-1,yes,buy,10,62,2024-11-12 18:00,0.10,"a, b"\nKX-A-1,Yes,Buy,5,0.70,2024-11-12 18:05,0.05,\n,,,,,,,\nKX-B-2,no,sell,3,30,x,0,\nKX-C-3,no,buy,4,"$0.25",y,0,';
  it("handles quotes, blank rows and unknown columns, and reports what it ignored", () => {
    expect(parseCsv('a,"b,c"\n1,"x""y"\r\n')).toEqual([["a", "b,c"], ["1", 'x"y']]);
    const p = parseKalshiCsv(csv);
    expect(p.columns.ticker).toBe("Ticker");
    expect(p.ignoredColumns).toEqual(["Fee", "Notes"]);
    expect(p.trades).toHaveLength(4);
    expect(p.trades[0]).toMatchObject({ ticker: "KX-A-1", side: "yes", action: "buy", count: 10, price: 0.62 });
    expect(p.trades[3]?.price).toBe(0.25);
  });
  it("averages buys by contracts, ignores sells", () => {
    const h = holdings(parseKalshiCsv(csv).trades);
    const a = h.find((x) => x.ticker === "KX-A-1");
    expect(a?.contracts).toBe(15);
    expect(a?.avgPrice).toBeCloseTo((10 * 0.62 + 5 * 0.7) / 15, 10);
    expect(h.some((x) => x.ticker === "KX-B-2")).toBe(false);
  });
  it("a file with no ticker column says so and yields nothing", () => {
    const p = parseKalshiCsv("foo,bar\n1,2");
    expect(p.trades).toEqual([]);
    expect(p.notes[0]).toContain("No ticker column");
    expect(parseKalshiCsv("").notes[0]).toContain("empty");
  });
});
