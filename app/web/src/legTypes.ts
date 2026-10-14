// Shapes returned by GET /api/breakdown/{game}/{player}/{stat} (app/api/src/parlayhitters_api/breakdown.py).

export type LegStat = "pts" | "reb" | "ast" | "fg3m" | "blk" | "stl" | "pra" | "pr" | "pa" | "ra" | "stocks";
export const LEG_STATS: readonly LegStat[] = ["pts", "reb", "ast", "fg3m", "blk", "stl", "pra", "pr", "pa", "ra", "stocks"];
export const LEG_SHORT: Record<LegStat, string> = {
  pts: "Pts",
  reb: "Reb",
  ast: "Ast",
  fg3m: "3PM",
  blk: "Blk",
  stl: "Stl",
  pra: "PRA",
  pr: "P+R",
  pa: "P+A",
  ra: "R+A",
  stocks: "Stocks",
};
export type Side = "over" | "under";

export function isLegStat(s: string): s is LegStat {
  return (LEG_STATS as readonly string[]).includes(s);
}

export interface Tally {
  n: number;
  hits: number;
  misses: number;
  pushes: number;
  rate: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  avg: number | null;
  avg_minutes: number | null;
  small: boolean;
}

export interface Chip {
  game_id: string;
  date: string;
  opp: string;
  /** Null when the data holds no schedule row for that older game. */
  home: boolean | null;
  value: number;
  minutes: number;
  starter: boolean;
  state: "hit" | "miss" | "push";
}

export interface Split {
  key: string;
  label: string;
  tonight: boolean;
  this_season: Tally;
  since_last_season: Tally;
}

export interface TeammateOut {
  player_id: number;
  name: string;
  status: string;
  reason: string | null;
  avg_minutes: number;
  without: Tally;
  with_: Tally;
  note: string;
}

export interface Teammates {
  report_as_of_utc: string | null;
  player_status: string | null;
  player_status_reason: string | null;
  items: TeammateOut[];
  omitted_unnamed: number;
  note: string;
}

export interface OppAllowed {
  opp: string;
  position: string;
  position_noun: string;
  avg_allowed: number;
  rank: number;
  of: number;
  n_games: number;
  league_avg: number;
  note: string;
}

export interface LegModel {
  available: boolean;
  p_hit: number | null;
  p_over: number | null;
  p_under: number | null;
  p_push: number | null;
  mean: number | null;
  typical: number | null;
  extrapolated: boolean;
  stamp: string | null;
  say: string;
  reason: string;
}

export interface LegRole {
  n: number;
  avg_minutes: number | null;
  starts: number;
  minutes_forecast: number | null;
  p_play: number | null;
  say: string;
}

/** One book's stored quote at the line on screen. A quote that is not usable has no number. */
export interface BookRow {
  book: string;
  over_price: number | null;
  under_price: number | null;
  over_unusable: boolean;
  under_unusable: boolean;
  alternate: boolean;
  minutes_before_tip: number | null;
}

export interface LegPrices {
  posted_line: number | null;
  over_price: number | null;
  under_price: number | null;
  on_posted_line: boolean;
  snapshot_utc: string | null;
  say: string;
  books: BookRow[];
}

export interface LadderRow {
  line: number;
  l10_over: number;
  l10_under: number;
  l10_push: number;
  l5_over: number;
  l5_under: number;
  l5_push: number;
  model_over: number | null;
  model_under: number | null;
  over_price: number | null;
  under_price: number | null;
}

export interface StatTab {
  stat: LegStat;
  label: string;
  has_model: boolean;
  posted_line: number | null;
  default_line: number;
  default_source: "posted" | "last10_middle" | "model_middle";
}

export interface Dissection {
  text: string;
  tier: "leg" | "parlay";
  provider: string;
  model: string | null;
  guard_ok: boolean;
  numbers_checked: number;
  fell_back: boolean;
  rejected: string[];
  disclosure: string;
}

export interface LegBreakdown {
  game_id: string;
  player_id: number;
  player: string;
  team: string;
  opponent: string;
  home: boolean;
  tip_utc: string;
  as_of_date: string;
  stat: LegStat;
  label: string;
  side: Side;
  line: number;
  line_source: "posted" | "last10_middle" | "model_middle";
  prior_games: number;
  last10: Tally;
  last5: Tally;
  chips: Chip[];
  season: Tally;
  splits: Split[];
  teammates: Teammates;
  opponent_allowed: OppAllowed | null;
  model: LegModel;
  role: LegRole;
  prices: LegPrices;
  heads_up: string | null;
  notes: string[];
  ladder: LadderRow[];
  ladder_n10: number;
  ladder_n5: number;
  tabs: StatTab[];
  dissection: Dissection | null;
}
