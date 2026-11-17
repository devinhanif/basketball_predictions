// Times and dates live in time.ts (one module for every conversion); re-exported here for existing imports.
export { STALE_AFTER_HOURS, ageNote, deskStamp, longDate, stamp, tipTime } from "./time";

export function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

/** P(over) shown with a floor/ceiling when the stored quantile grid does not reach that far. */
export function pctBounded(p: number, extrapolated: boolean): string {
  if (!extrapolated) return pct(p);
  if (p >= 0.9) return "95%+";
  if (p <= 0.1) return "<5%";
  return pct(p);
}

export function american(price: number): string {
  return price > 0 ? `+${price}` : `${price}`;
}

const BOOKS: Record<string, string> = {
  draftkings: "DraftKings",
  fanduel: "FanDuel",
  betmgm: "BetMGM",
  caesars: "Caesars",
  espnbet: "ESPN Bet",
  fanatics: "Fanatics",
  kalshi: "Kalshi",
  novig: "Novig",
  polymarket: "Polymarket",
  polymarket_us: "Polymarket US",
};

/** The book's name as people write it; an unknown key is shown as the vendor sent it. */
export function bookName(key: string): string {
  return BOOKS[key] ?? key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
}

export function num(x: number, digits = 1): string {
  return x.toFixed(digits).replace(/\.0$/, "");
}

export function lineText(line: number): string {
  return Number.isInteger(line) ? line.toFixed(0) : line.toFixed(1);
}

export function signed(x: number, digits = 4): string {
  return `${x >= 0 ? "+" : "-"}${Math.abs(x).toFixed(digits)}`;
}

/* ------------------------------------------------------------------ the one number formatter
   Every function below takes a stored number and only formats it: no arithmetic beyond unit conversion for display.
   A missing value (null, undefined, NaN, Infinity) is shown as MISSING, never as 0. Negative zero is shown as zero.
   Negative numbers carry a real minus sign (U+2212), which screen readers say as "minus". */

/** What a missing number looks like on screen. */
export const MISSING = "—";
/** The minus sign used for negative numbers. */
export const MINUS = "−";

export type OddsFormat = "american" | "decimal";

function ok(x: number | null | undefined): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

/** Fixed digits with no "-0" and a real minus sign. */
function fixed(x: number, digits: number): string {
  const s = Math.abs(x).toFixed(digits);
  const zero = Number(s) === 0;
  return x < 0 && !zero ? `${MINUS}${s}` : s;
}

/** A chance (0..1) as a percent: 0.3649 -> "36%"; digits after the point on request. */
export function percent(p: number | null | undefined, digits = 0): string {
  return ok(p) ? `${fixed(p * 100, digits)}%` : MISSING;
}

/** A plain number with up to `digits` decimals, trailing ".0" dropped: 26.04 -> "26", -1.5 -> "−1.5". */
export function decimal(x: number | null | undefined, digits = 1): string {
  return ok(x) ? fixed(x, digits).replace(/\.0+$/, "") : MISSING;
}

/** A signed change: always a sign, "+" for positive, U+2212 for negative, none for zero. */
export function delta(x: number | null | undefined, digits = 1, unit = ""): string {
  if (!ok(x)) return MISSING;
  const body = Math.abs(x).toFixed(digits);
  if (Number(body) === 0) return `${body}${unit}`;
  return `${x < 0 ? MINUS : "+"}${body}${unit}`;
}

/** An amount already in cents: 51.4 -> "51.4c", 51 -> "51c". */
export function centsText(c: number | null | undefined, digits = 1): string {
  return ok(c) ? `${fixed(c, digits).replace(/\.0+$/, "")}c` : MISSING;
}

/** A signed amount already in cents: -7.5 -> "−7.5c", 3 -> "+3c". */
export function deltaCents(c: number | null | undefined, digits = 1): string {
  if (!ok(c)) return MISSING;
  return delta(c, digits).replace(/\.0+$/, "") + "c";
}

/** A count with thousands separators, as an integer: 12345 -> "12,345". */
export function count(n: number | null | undefined): string {
  if (!ok(n)) return MISSING;
  const r = Math.round(n);
  const s = Math.abs(r).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return r < 0 ? `${MINUS}${s}` : s;
}

/** "n = 1,234": the sample size beside a claim. */
export function nText(n: number | null | undefined): string {
  return `n = ${count(n)}`;
}

/** An American price shown in the visitor's chosen format. Decimal is the same price written as total return per 1. */
export function odds(price: number | null | undefined, format: OddsFormat = "american"): string {
  if (!ok(price) || price === 0) return MISSING;
  if (format === "american") return price > 0 ? `+${Math.round(price).toString()}` : `${MINUS}${Math.abs(Math.round(price)).toString()}`;
  const dec = price > 0 ? 1 + price / 100 : 1 + 100 / -price;
  return dec.toFixed(2);
}
