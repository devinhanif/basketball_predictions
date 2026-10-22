// Shapes returned by GET /api/live/* (app/api/src/parlayhitters_api/live.py).

export type GameState = "pre" | "in" | "post";

export interface LiveStatus {
  state: GameState;
  period: number;
  clock: string;
  detail: string;
  completed: boolean;
}

export interface LiveTeam {
  side: "home" | "away";
  abbr: string;
  name: string;
  score: number | null;
}

export interface LiveGameRow {
  event_id: string;
  start_utc: string;
  et_date: string | null;
  status: LiveStatus;
  home: LiveTeam;
  away: LiveTeam;
}

export interface Scoreboard {
  source: string;
  fetched_at: string;
  stale: boolean;
  age_s: number;
  games: LiveGameRow[];
}

export type FcStat = "pts" | "reb" | "ast" | "fg3m";
export const FC_STATS: readonly FcStat[] = ["pts", "reb", "ast", "fg3m"];

export interface BoxPlayer {
  player_id: string;
  name: string;
  team: string;
  starter: boolean;
  dnp: boolean;
  min: string | null;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  fg3m: number | null;
  pf: number | null;
  pm: string | null;
  forecast: Partial<Record<FcStat, { mean: number; line: number | null }>> | null;
}

export interface LiveGame {
  event_id: string;
  source: string;
  fetched_at: string;
  stale: boolean;
  age_s: number;
  status: LiveStatus;
  home: LiveTeam | null;
  away: LiveTeam | null;
  players: BoxPlayer[];
  box_available: boolean;
  forecast_date: string | null;
  forecast_note: string | null;
}

export interface KalshiMarket {
  ticker: string;
  title: string;
  status: string;
  result: string | null;
  yes_bid: number | null;
  yes_ask: number | null;
  no_bid: number | null;
  no_ask: number | null;
  last: number | null;
  found: boolean;
}

export interface KalshiPrices {
  source: string;
  fetched_at: string;
  stale: boolean;
  age_s: number;
  markets: KalshiMarket[];
}
