// Options-style views of a parlay: value over time, payoff by leg, and leg deltas. Pure functions, no drawing.

export interface HistPoint {
  t: string;
  yes_bid: number | null;
  yes_ask: number | null;
  src: "capture" | "sample";
}

export interface History {
  capture: boolean;
  source: string;
  series: Record<string, HistPoint[]>;
}

export function pointMid(p: HistPoint): number | null {
  if (p.yes_bid !== null && p.yes_ask !== null) return (p.yes_bid + p.yes_ask) / 2;
  return p.yes_bid ?? p.yes_ask;
}

export interface ValuePoint {
  /** Milliseconds since the epoch. */
  t: number;
  value: number;
}

export interface SettleMark {
  leg: number;
  t: number;
  kind: "hit" | "dead";
}

export interface ComboSeries {
  points: ValuePoint[];
  marks: SettleMark[];
  /** Legs left out because they have no ticker or no history. */
  missing: number[];
}

export const HIT_AT = 0.97;
export const DEAD_AT = 0.03;

/**
 * Combo value over time: shares x product of leg mids, each leg's last known mid carried forward. It starts at the
 * first time every leg has a price. A leg "settles" in the chart where its price crosses 97c (or 3c) and stays there to
 * the end of its history: inferred from the price, not read from a settlement record.
 */
export function comboSeries(tickers: string[], hist: Record<string, HistPoint[] | undefined>, shares: number): ComboSeries {
  const per = tickers.map((tk) => {
    const pts = (tk === "" ? [] : (hist[tk] ?? []))
      .map((p) => ({ t: Date.parse(p.t), m: pointMid(p) }))
      .filter((p): p is { t: number; m: number } => Number.isFinite(p.t) && p.m !== null)
      .sort((a, b) => a.t - b.t);
    return pts;
  });
  const missing = per.flatMap((p, i) => (p.length === 0 ? [i] : []));
  if (per.length === 0 || missing.length > 0) return { points: [], marks: [], missing };
  const times = [...new Set(per.flatMap((p) => p.map((x) => x.t)))].sort((a, b) => a - b);
  const idx = per.map(() => -1);
  const points: ValuePoint[] = [];
  for (const t of times) {
    per.forEach((p, i) => {
      while (idx[i] !== undefined && (idx[i]) + 1 < p.length && (p[(idx[i]) + 1] as { t: number }).t <= t) idx[i] = (idx[i]) + 1;
    });
    if (idx.some((k) => k < 0)) continue;
    const v = per.reduce((a, p, i) => a * (p[idx[i] as number] as { m: number }).m, 1);
    points.push({ t, value: v * shares });
  }
  const marks: SettleMark[] = [];
  per.forEach((p, leg) => {
    const last = p[p.length - 1];
    if (!last) return;
    const kind = last.m >= HIT_AT ? "hit" : last.m <= DEAD_AT ? "dead" : null;
    if (kind === null) return;
    let k = p.length - 1;
    while (k > 0 && (kind === "hit" ? (p[k - 1] as { m: number }).m >= HIT_AT : (p[k - 1] as { m: number }).m <= DEAD_AT)) k--;
    marks.push({ leg, t: (p[k] as { t: number }).t, kind });
  });
  return { points, marks, missing: [] };
}

export interface Delta {
  leg: number;
  /** Change in combo value ($) per 1-cent move of this leg's price: shares x product of the other legs / 100. */
  perCent: number;
}

/** `values` are each leg's current value in 0..1 (1 hit, 0 dead, else the mid). Sorted by size, largest first. */
export function legDeltas(shares: number, values: (number | null)[], skip: Set<number> = new Set()): Delta[] {
  const out: Delta[] = [];
  values.forEach((_, i) => {
    if (skip.has(i)) return;
    const others = values.filter((_, j) => j !== i);
    if (others.some((v) => v === null)) return;
    out.push({ leg: i, perCent: (shares * others.reduce((a: number, v) => a * (v ?? 0), 1)) / 100 });
  });
  return out.sort((a, b) => b.perCent - a.perCent);
}

export interface PayoffRow {
  leg: number;
  /** Combo value if this leg hits (the other legs at their current value). */
  ifHit: number;
  /** Combo value if this leg misses. */
  ifMiss: number;
}

export function payoffRows(shares: number, values: (number | null)[], skip: Set<number> = new Set()): PayoffRow[] {
  return legDeltas(shares, values, skip)
    .map((d) => ({ leg: d.leg, ifHit: d.perCent * 100, ifMiss: 0 }))
    .sort((a, b) => a.leg - b.leg);
}
