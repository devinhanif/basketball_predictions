// Top-parlays panel: shapes of /api/top-parlays/<date> (contract docs/contracts/top_parlays_v2.md)
// and the small pure helpers the panel uses. No number is made up here: all come from the payload.
import type { Side } from "./legTypes";

export interface TpLeg {
  game_id: string;
  tipoff: string;
  player_id: number;
  player_name: string;
  team: string;
  opp: string;
  stat: string;
  side: Side;
  line: number;
  book: string;
  price_american: number;
  price_at: string;
  p_raw: number;
  p_cal: number;
  model_source: string;
  reason: string;
  /** Optional in the file; the API fills false. */
  tail_flag?: boolean;
}

export interface TpTicket {
  rank: number;
  same_game: boolean;
  legs: TpLeg[];
  p_joint_raw: number;
  p_joint_cal: number;
  p_joint_cal_80: [number, number];
  book_implied: number;
  ev_per_dollar: number;
  ev_low80: number;
  why: string;
  /** Optional in the file; the API fills null. */
  tail_note?: string | null;
}

export interface TpGate {
  settled: number;
  settled_min: number;
  slate_dates: number;
  dates_min: number;
  skill_vs_market: number | null;
  open: boolean;
}

export interface TopParlays {
  schema: "top_parlays/2";
  illustration: boolean;
  slate_date: string;
  made_at: string;
  calibration: { rule: string; sha8: string; fit_through: string; status: string };
  gate: TpGate;
  ranking: string;
  verdict: string;
  empty_reason: string | null;
  sizes: Record<"2" | "3", TpTicket[]>;
  fixture?: { kind: string; label: string } | null;
  caveats: string[];
}

export type TpResult =
  | { kind: "ok"; data: TopParlays }
  | { kind: "missing" }
  | { kind: "refused"; detail: string };

const ET_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** The Eastern calendar date (YYYY-MM-DD) of an instant: slate dates are ET dates. */
export function etDate(when: string | Date): string {
  return ET_DATE.format(typeof when === "string" ? new Date(when) : when);
}

/** Stale: the file was made before its slate's Eastern date began. */
export function isStale(madeAt: string, slateDate: string): boolean {
  return etDate(madeAt) < slateDate;
}

/** Made at or after the first tip: not a forecast. */
export function madeAfterTip(d: TopParlays): boolean {
  const tips = [...d.sizes["2"], ...d.sizes["3"]].flatMap((t) => t.legs.map((l) => Date.parse(l.tipoff)));
  return tips.length > 0 && Date.parse(d.made_at) >= Math.min(...tips);
}

export function hasTickets(d: TopParlays): boolean {
  return d.sizes["2"].length + d.sizes["3"].length > 0;
}

/** Dollars won or lost per $1 staked, signed, e.g. "+$0.05" or "-$0.24". */
export function evText(x: number): string {
  const r = Math.round(x * 100) / 100;
  return `${r >= 0 ? "+" : "-"}$${Math.abs(r).toFixed(2)}`;
}

export function isKeepYourMoney(verdict: string): boolean {
  return verdict === "keep_your_money";
}
