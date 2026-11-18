// Shapes of the combo payload (docs/contracts/combo_export_v1.md) and of what the engine returns. No UI imports.

export type ComboStat = "pts" | "reb" | "ast" | "fg3m";
export const COMBO_STATS: readonly ComboStat[] = ["pts", "reb", "ast", "fg3m"];
export type ScriptName = "blowout" | "close" | "shootout" | "grind";
export const SCRIPTS: readonly ScriptName[] = ["blowout", "close", "shootout", "grind"];

export interface StatGrid {
  k0: number;
  /** surv[j] = S(k0 + j) = P(Y >= k0 + j); S(k) = 1 below k0, 0 past the end. */
  surv: readonly number[];
  main_line: number | null;
  /** The uncalibrated chance at the main line. Used only by the tested-range rule; never displayed. */
  raw_at_main?: number | null;
  /** [lo, hi]: 1-based rungs where the calibration map was applied directly. */
  cal_k: readonly [number, number];
  /** Optional (not in contract v1): where the posted main line opened, a number only. */
  main_line_open?: number | null;
}

export interface ComboPlayer {
  player_id: number;
  name: string;
  team: number;
  p_play: number | null;
  starter: 0 | 1;
  stats: Partial<Record<ComboStat, StatGrid>>;
}

export interface ComboGame {
  game_id: string;
  date: string;
  home_team: number;
  away_team: number;
  tip_utc: string;
  made_at: string;
  game: { mu_m: number; sd_m: number; mu_t: number; sd_t: number };
  players: readonly ComboPlayer[];
}

export type CorrRow = readonly [string, string, string, number, number];
export type ParamRow = readonly [string, string, string, number, number, number];

export interface ComboShared {
  corr: readonly CorrRow[];
  scripts: {
    definitions: { blowout_abs_margin: number; close_abs_margin: number; total_delta: number };
    params: readonly ParamRow[];
    status: Record<string, { status: string; why_not?: readonly string[] } | string>;
    /** Optional (not in contract v1): how many games each script occurred in, per script. */
    history?: Partial<Record<ScriptName, { n_games: number; n_occurred: number }>>;
  };
  provenance?: { corr_n_games?: number; corr_fit_seasons?: readonly number[]; [k: string]: unknown };
}

export interface ComboPayload {
  schema: "combo_export_v1";
  night: string;
  fixture?: { label: string };
  shared: ComboShared;
  games: readonly ComboGame[];
}

/** What GET /api/combo/{date} answers: the stored file, unchanged, plus the server's clock. */
export interface ComboEnvelope {
  mode: string;
  clock_utc: string;
  combo: ComboPayload;
}

export interface PlayerLeg {
  kind: "player";
  game_id: string;
  player_id: number;
  stat: string;
  line: number;
  side: "over" | "under";
  /** Search candidates carry their marginal here (as in the reference). */
  p?: number;
}
export interface TeamMarginLeg {
  kind: "team_margin";
  game_id: string;
  team: number;
  gt: number;
}
export interface TotalLeg {
  kind: "total";
  game_id: string;
  line: number;
  side: "over" | "under";
}
export type Leg = PlayerLeg | TeamMarginLeg | TotalLeg;

export interface ComboResult {
  p_joint: number;
  p_independent: number;
  lift: number | null;
  marginals: number[];
  se: number;
  method: "marginal" | "exact_bivariate" | "qmc" | "product" | "product_of_games" | "empty";
  p_all_play: number | null;
  p_joint_incl_dnp: number | null;
  repair_t: number;
  script: string | null;
  n_entities: number;
  label: string | null;
  range_2se: [number, number] | null;
  above_tested_range: boolean[];
}

export interface RankedCombo {
  legs: PlayerLeg[];
  side: "over" | "under";
  p_joint: number;
  p_independent: number;
  lift: number;
  se: number;
  method: string;
  reason: string;
  label: string | null;
  range_2se: [number, number] | null;
  above_tested_range: boolean;
}

export interface TargetCombo {
  legs: PlayerLeg[];
  p_joint: number;
  p_independent: number;
  lift: number | null;
  se: number;
  method: string;
  label: string | null;
  range_2se: [number, number] | null;
}

export interface TargetResult {
  target: number;
  k_legs: number;
  band: [number, number];
  floor_mode: string;
  objective: string;
  feasible: boolean;
  combos: TargetCombo[];
}
