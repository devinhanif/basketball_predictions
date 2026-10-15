// Small pure helpers for the leg breakdown screen. They read numbers the API already computed;
// none of them invents one. (The hit/miss rule itself lives in the API, once.)
import type { LadderRow, LegPrices, Side, Tally } from "./legTypes";
import { devigOver } from "./stats";

/** The ladder row for a line, if the API computed one at exactly that line. */
export function ladderAt(ladder: readonly LadderRow[], line: number): LadderRow | null {
  return ladder.find((r) => r.line === line) ?? null;
}

export function rowHits(row: LadderRow, side: Side, window: 10 | 5): number {
  if (window === 10) return side === "over" ? row.l10_over : row.l10_under;
  return side === "over" ? row.l5_over : row.l5_under;
}

export function rowModel(row: LadderRow, side: Side): number | null {
  return side === "over" ? row.model_over : row.model_under;
}

export function rowPrice(row: LadderRow, side: Side): number | null {
  return side === "over" ? row.over_price : row.under_price;
}

/** A model chance. The stored grid stops at 5% and 95%, so those two ends read as bounds, not points. */
export function modelPct(p: number): string {
  if (p >= 0.95 - 1e-9) return "95%+";
  if (p <= 0.05 + 1e-9) return "<5%";
  return `${Math.round(p * 100).toString()}%`;
}

/** "6 of 10". */
export function ofText(hits: number, n: number): string {
  return `${hits.toString()} of ${n.toString()}`;
}

/** "4/6" for a split cell. */
export function ratio(t: Tally): string {
  return `${t.hits.toString()}/${t.n.toString()}`;
}

/** "30% to 83%": the 95% interval around a hit rate, or null when there is no sample. */
export function rangeText(t: Tally): string | null {
  if (t.ci_lo === null || t.ci_hi === null || t.n === 0) return null;
  return `${Math.round(t.ci_lo * 100).toString()}% to ${Math.round(t.ci_hi * 100).toString()}%`;
}

export function ordinal(n: number): string {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n.toString()}th`;
  switch (n % 10) {
    case 1:
      return `${n.toString()}st`;
    case 2:
      return `${n.toString()}nd`;
    case 3:
      return `${n.toString()}rd`;
    default:
      return `${n.toString()}th`;
  }
}

/** The sample-size marker printed beside every rate. */
export function nText(t: Tally): string {
  return t.small ? `n = ${t.n.toString()}, small sample` : `n = ${t.n.toString()}`;
}

export function clampLine(line: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, line));
}

/** Snap to the half-point grid the API builds its ladder on. */
export function snapHalf(x: number): number {
  return Math.round(x * 2) / 2;
}

/** Where the rank sits in plain words: rank 1 allows the most, so it is the best spot for overs. */
export function rankPhrase(rank: number, of: number): string {
  if (rank <= 5) return `${ordinal(rank)} most of ${of.toString()}: a soft spot`;
  if (rank > of - 5) return `${ordinal(rank)} most of ${of.toString()}: a tough spot`;
  return `${ordinal(rank)} most of ${of.toString()}`;
}

/**
 * The book's chance for the side on screen, margin removed, and whose price it is (F13). Null when no single book
 * quoted both sides at that line: nothing is invented. A slider line uses that line's own stored pair.
 */
export function bookChance(
  prices: LegPrices,
  onScreenLine: boolean,
  row: LadderRow | null,
  side: Side,
): { p: number; book: string } | null {
  const book = prices.market_book;
  if (!book) return null;
  let over: number | null = null;
  if (onScreenLine && prices.market_p_over != null) over = prices.market_p_over;
  else if (row && row.over_price !== null && row.under_price !== null) over = devigOver(row.over_price, row.under_price);
  if (over === null) return null;
  return { p: side === "over" ? over : 1 - over, book };
}
