// Shapes returned by the read-only API (app/api/src/parlayhitters_api/models.py).
import type { PlayerRole } from "./roleTypes";

export type Stat = "pts" | "reb" | "ast" | "fg3m";
export const STATS: readonly Stat[] = ["pts", "reb", "ast", "fg3m"];
export const STAT_LABEL: Record<Stat, string> = {
  pts: "Points",
  reb: "Rebounds",
  ast: "Assists",
  fg3m: "3-pointers",
};
export const STAT_SHORT: Record<Stat, string> = { pts: "pts", reb: "reb", ast: "ast", fg3m: "3pm" };

export interface Team {
  id: number;
  abbr: string;
  city: string;
  name: string;
}

export interface WinProbability {
  model_home: number | null;
  model_name: string;
  model_stamp: string | null;
  market_home: number | null;
  market_name: string;
  market_snapshot_utc: string | null;
}

export interface KeyProp {
  player_id: number;
  name: string;
  team: string;
  stat: Stat;
  line: number;
  over_price: number | null;
  under_price: number | null;
  forecast_mean: number | null;
  /** The forecast's middle and middle half (stored quantiles 0.25, 0.50, 0.75). */
  q25: number;
  q50: number;
  q75: number;
  model_p_over: number;
  /** Null when the data holds no sportsbook price (the nightly export). */
  market_p_over: number | null;
  line_source: "posted" | "reference";
  extrapolated: boolean;
  model_source: string;
  model_label: string;
}

export interface GameSummary {
  game_id: string;
  date: string;
  tip_utc: string;
  arena: string | null;
  home: Team;
  away: Team;
  win_probability: WinProbability;
  key_props: KeyProp[];
}

export interface Slate {
  date: string;
  label: string;
  clock_utc: string;
  mode: string;
  counts: { games: number; players: number; props: number };
  games: GameSummary[];
}

export interface SlateListItem {
  date: string;
  label: string;
  games: number;
  props: number;
}

export interface PropView {
  prop_id: string;
  stat: Stat;
  line: number;
  /** "posted" is a sportsbook line; "reference" is the middle of our own forecast (no market line). */
  line_source: "posted" | "reference";
  over_price: number | null;
  under_price: number | null;
  /** The one book whose over and under are shown. */
  price_book: string | null;
  line_snapshot_utc: string | null;
  forecast_mean: number | null;
  q: number[];
  taus: number[];
  model_p_over: number;
  model_p_under: number;
  model_p_push: number;
  extrapolated: boolean;
  market_p_over: number | null;
  forecast_stamp: string;
  /** Which model the forecast came from, in plain words (the API writes the sentence). */
  model_source: string;
  model_label: string;
  /** Stored model name and version, verbatim (tooltip). */
  model_detail: string | null;
  /** Chance to play (0 to 1) and the sentence naming its source; null when none is stored. */
  p_play: number | null;
  p_play_label: string | null;
  /** Optional export fields (docs/contracts/rung_cal_export_v1.md); null when the export holds none. */
  book_main_line?: number | null;
  rung_cal?: RungCal | null;
}

export interface PlayerView {
  player_id: number;
  name: string;
  side: "home" | "away";
  props: Partial<Record<Stat, PropView>>;
  /** Minutes steadiness, on/off and without-teammate numbers; null when no history is loaded. */
  role?: PlayerRole | null;
}

export interface GameDetail {
  game_id: string;
  date: string;
  tip_utc: string;
  arena: string | null;
  home: Team;
  away: Team;
  win_probability: WinProbability;
  clock_utc: string;
  /** False until the game is over (an export also holds tonight's games). */
  final: boolean;
  players: PlayerView[];
  notes: string[];
}

export interface GameResult {
  game_id: string;
  label: string;
  home_pts: number;
  away_pts: number;
  home_won: boolean;
  players: {
    player_id: number;
    minutes: number | null;
    starter: boolean | null;
    actual: Partial<Record<Stat, number>>;
  }[];
}

export interface SwipeCardData {
  prop_id: string;
  game_id: string;
  player_id: number;
  player: string;
  team: string;
  opponent: string;
  stat: Stat;
  line: number;
  over_price: number;
  under_price: number;
  book: string | null;
  tip_utc: string;
}

export interface SwipeDeck {
  date: string;
  clock_utc: string;
  seed: number;
  cards: SwipeCardData[];
  note: string;
}

export interface ResultRow {
  prop_id: string;
  game_id: string;
  player: string;
  stat: Stat;
  line: number;
  over_price: number;
  under_price: number;
  actual: number;
  model_p_over: number;
  model_p_under: number;
  market_p_over: number;
  market_p_under: number;
}

export interface SlateResults {
  date: string;
  label: string;
  rows: ResultRow[];
}

export interface Meta {
  mode: string;
  mode_note: string;
  data_version: string;
  built_at: string;
  /** When the data was built, from the export's manifest (or the fixture build). */
  data_as_of: string;
  export_date: string | null;
  max_season: number;
  slates: string[];
  stubs: string[];
  sources: Record<string, string>;
  queries: Record<string, string>;
}

export interface ParlayState {
  state: "A" | "B" | "C";
  title: string;
  headline_verdict: string;
  verdict_line: string;
  as_of: string;
  report_date: string;
  source: string;
  engine: string;
  reason: string;
  counts: Record<string, number>;
  /** Null when the export carries only the engine's verdict and counts. */
  gate: {
    n_settled: number;
    n_dates: number;
    min_settled: number;
    min_settled_dates: number;
    skill_vs_market: number;
    gate_open: boolean;
    describe: string;
  } | null;
  model_weight: number | null;
  model_weight_note: string;
  fee_model: { name: string; formula: string; coefficient: number; verified: boolean; source: string } | null;
  price_basis: string;
  historical_note: string;
  disclaimer: string;
}

export interface ProbAtLine {
  game_id: string;
  player_id: number;
  stat: Stat;
  line: number;
  p_over: number;
  p_under: number;
  p_push: number;
  extrapolated: boolean;
  method: string;
  main_line: number;
  is_main_line: boolean;
  over_price: number | null;
  under_price: number | null;
}

export interface RungCal {
  book: string;
  main_line: number;
  price_at: string | null;
  map_version: string;
  map_sha8: string;
  lines: { line: number; p_over: number }[];
}

/** P(Y >= k) for k = 0, 1, 2, ...: P(over k.5) = p_ge[k + 1]. */
export interface Dist {
  method: "exact" | "quantile";
  n: number | null;
  p_ge: number[];
  line_min: number;
  line_max: number;
  mean: number | null;
}

export interface CardStats {
  as_of_date: string;
  prior_games: number;
  /** Most recent first. */
  last5: number[];
  last10: number[];
  /** The same ten games with their ids; `linkable` means the data holds that game's page. */
  last10_games: { game_id: string; date: string; opp: string; home: boolean | null; value: number; linkable: boolean }[];
  avg5: number | null;
  avg10: number | null;
  avg_season: number | null;
  n_season: number;
  min5: number | null;
  min_season: number | null;
  hit10_over: { n: number; hits: number; pushes: number; line: number };
  teammates_out: { name: string; status: string }[];
  teammates_known: boolean;
}

export interface CardView {
  prop_id: string;
  game_id: string;
  player_id: number;
  player: string;
  team: string;
  opponent: string;
  home: boolean;
  stat: Stat;
  line: number;
  line_source: "posted" | "reference";
  over_price: number | null;
  under_price: number | null;
  price_book: string | null;
  book_main_line: number | null;
  rung_cal: RungCal | null;
  dist: Dist;
  stats: CardStats | null;
  forecast_stamp: string;
  model_label: string;
  tip_utc: string;
}
