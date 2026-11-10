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
  /** Calibrated (line_cal, rung_cal) or the model's own number with no calibrated value (model). */
  model_p_source: PSource;
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
  /** Set only by the single-file snapshot: false when the night has no swipe deck to grade picks against. */
  deck?: boolean;
  /** Games with a final score in the data (absent in the single-file snapshot). */
  finished?: number;
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
  /** Where model_p_over came from: the calibrated number, or the model's own with no calibrated value. */
  model_p_source: PSource;
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
  /** docs/contracts/line_cal_export_v1.md; a missing field reads as null (older exports). */
  line_cal?: LineCal | null;
  line_cal_null_reason?: string | null;
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
  /** Why there are no cards; absent in older data and in the single-file snapshot. */
  reason?: "ok" | "no_games" | "all_started" | "no_lines";
  games_total?: number;
  games_started?: number;
  games_with_lines?: number;
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
  /** The calibrated number (line_cal, rung_cal) or the model's own with no calibrated value (model). */
  p_source: PSource;
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

/** LINE_CALIBRATION (T493) at the DraftKings main line only; the under side is 1 - p_over_cal. */
export interface LineCal {
  book: string;
  main_line: number;
  price_at: string | null;
  p_over_raw: number;
  p_over_cal: number;
  map: string;
  map_sha8: string;
  fit_through: string | null;
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
  line_cal?: LineCal | null;
  line_cal_null_reason?: string | null;
  dist: Dist;
  stats: CardStats | null;
  forecast_stamp: string;
  model_label: string;
  model_source: string;
  tip_utc: string;
  /** Projected and recent minutes and the rough share of the spread they explain (absent in older servers). */
  minutes?: MinutesInfo | null;
}

export interface MinutesInfo {
  baseline: number | null;
  baseline_source: "model" | "recent" | null;
  proj_minutes: number | null;
  p_play: number | null;
  recent_avg: number | null;
  recent_sd: number | null;
  recent_n: number;
  stat_mean: number | null;
  stat_sd: number | null;
  minutes_share: number | null;
  range_now: [number, number];
  range_minutes_known: [number, number] | null;
  method: string;
}

export type PSource = "line_cal" | "rung_cal" | "model";

/* ------------------------------------------------------------------ track record */
export interface CalBin {
  lo: number;
  hi: number;
  n: number;
  n_games: number;
  said: number | null;
  happened: number | null;
  lo95: number | null;
  hi95: number | null;
  enough: boolean;
}
export interface PubInterval {
  mean: number;
  lo: number;
  hi: number;
}
export interface PubBook {
  label: string;
  n: number;
  games: number;
  log_loss_cal_minus_raw: PubInterval;
  mean_cal_minus_realised: PubInterval;
  bins: { lo: number; n: number; said: number; happened: number }[];
}
export interface PropsPublished {
  ledger_id: string;
  season_label: string;
  rule_sha8: string;
  verdict: string;
  source: string;
  books: Record<string, PubBook>;
  interval: string;
  this_season: string;
}
export interface CalSeries {
  name: string;
  what_said: string;
  what_happened: string;
  n: number;
  n_games: number;
  bins: CalBin[];
  overall: CalBin;
  sources: { source: string; label: string; n: number; overall: CalBin }[];
  excluded: Record<string, number>;
  interval: string;
}
export interface Calibration {
  mode: string;
  date_from: string | null;
  date_to: string | null;
  n_games_finished: number;
  /** The calibrated map's published record on a finished season; nothing computed from this season. */
  props: PropsPublished;
  winners: CalSeries;
  note: string;
}
export interface GuessCard {
  prop_id: string;
  game_id: string;
  date: string;
  player: string;
  team: string;
  opponent: string;
  home: boolean;
  stat: Stat;
  line: number;
  form: { last5: number[]; avg_minutes5: number | null; n_prior: number } | null;
}
export interface GuessDeck {
  seed: number;
  cards: GuessCard[];
  n_available: number;
  reason: string;
  note: string;
}
export interface GuessReveal {
  prop_id: string;
  actual: number;
  line: number;
  went_over: boolean;
  model_p_over: number;
  model_p_source: PSource;
  model_call: "over" | "under";
  minutes: number | null;
  stat_mean: number;
  forecast_stamp: string;
}

/* ------------------------------------------------------------------ what the game knows */
export interface KnowsSnap {
  made_at: string;
  lead_minutes: number;
  mean: number | null;
  p10: number;
  p50: number;
  p90: number;
  /** Calibrated chance on the shown forecast only; null on earlier snapshots (none exists for them). */
  p_over: number | null;
  proj_minutes: number | null;
  p_play: number | null;
  shown: boolean;
}
export interface KnowsNews {
  at_utc: string;
  lead_minutes: number | null;
  player_id: number;
  name: string;
  team: string | null;
  status: string;
  previous: string | null;
  reason: string | null;
  himself: boolean;
  first_report: boolean;
}
export interface UncItem {
  key: string;
  text: string;
}
export interface PlayerKnows {
  game_id: string;
  player_id: number;
  player: string;
  team: string;
  stat: Stat;
  line: number;
  line_source: string;
  p_over_source: PSource;
  tip_utc: string;
  final: boolean;
  snapshots: KnowsSnap[];
  news: KnowsNews[];
  uncertainty: { p10: number; p90: number; mean: number | null; items: UncItem[]; minutes: MinutesInfo };
  note: string;
}
export interface KnowsMover {
  player_id: number;
  name: string;
  team: string;
  stat: Stat;
  first_mean: number;
  last_mean: number;
  delta: number;
  first_at: string;
  last_at: string;
  n_snapshots: number;
}
export interface GameKnows {
  game_id: string;
  tip_utc: string;
  news: KnowsNews[];
  movers: KnowsMover[];
  n_reports: number;
  note: string;
}
