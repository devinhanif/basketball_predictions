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
