// Top-parlays panel: shapes of /api/top-parlays/<date> (contract docs/contracts/top_parlays_v1.md)
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
}

export interface TopParlays {
  schema: "top_parlays/1";
  illustration?: boolean;
  slate_date: string;
  made_at: string;
  calibration: { rule: string; sha8: string; fit_through: string; status: string };
  ranking: string;
  verdict: string;
  empty_reason: string | null;
  sizes: Record<"2" | "3", TpTicket[]>;
  caveats: string[];
}

export type TpResult =
  | { kind: "ok"; data: TopParlays }
  | { kind: "missing" }
  | { kind: "refused"; detail: string };

/** Stale: the file was made before its slate day began (UTC date of made_at earlier than the slate date). */
export function isStale(madeAt: string, slateDate: string): boolean {
  return madeAt.slice(0, 10) < slateDate;
}

export function hasTickets(d: TopParlays): boolean {
  return d.sizes["2"].length + d.sizes["3"].length > 0;
}

/** Dollars won or lost per $1 staked, signed, e.g. "+$0.05" or "-$0.24". */
export function evText(x: number): string {
  return `${x >= 0 ? "+" : "-"}$${Math.abs(x).toFixed(2)}`;
}

export function isKeepYourMoney(verdict: string): boolean {
  return verdict === "keep_your_money";
}
