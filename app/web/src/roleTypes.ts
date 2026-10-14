// Shapes of the role facts from the API (app/api/src/parlayhitters_api/role_stats.py and role.py):
// minutes steadiness, team on/off, and a player's numbers without a teammate who is out tonight.

export type SteadyLabel = "Steady" | "Mixed" | "Limited data";

export interface Steadiness {
  label: SteadyLabel;
  /** Games he played, out of the window. */
  n: number;
  window: number;
  avg_minutes: number | null;
  sd_minutes: number | null;
  cv: number | null;
  say: string;
  caveat: string;
}

/** His minutes and per-36 numbers in one set of games. Assists come from the box score. */
export interface Per36 {
  n: number;
  avg_minutes: number | null;
  pts36: number | null;
  reb36: number | null;
  ast36: number | null;
  small: boolean;
}

/** Team points per 100 possessions while he is on (or off) the floor. */
export interface Rate {
  poss: number;
  off_poss: number;
  def_poss: number;
  ortg: number | null;
  drtg: number | null;
  net: number | null;
}

export interface OnOffWindow {
  games: number;
  on: Rate;
  off: Rate;
  diff: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  small: boolean;
  note: string;
}

export interface OnOff {
  available: boolean;
  this_season: OnOffWindow;
  since_last_season: OnOffWindow;
  note: string;
}

export type BeyondKey = "min" | "pts" | "reb" | "ast" | "fg3m";

/** One stat of the shrunk with/without headline (api wowy_shrink.py). */
export interface BeyondStat {
  stat: BeyondKey;
  label: string;
  /** Games without him minus games with him, per game: the raw history. */
  past_gap: number | null;
  /** What past seasons say carries into tonight beyond the forecast; 0 when there is no effect. */
  expected_extra: number | null;
  no_effect: boolean;
  /** "about 1 in this many" of a gap carried over. */
  one_in: number | null;
  large_gap: boolean;
  /** Large gap and the coefficient measured inside the biggest gaps spans 0: a zero, in words. */
  no_carry_large: boolean;
  window: string;
}

export interface Beyond {
  available: boolean;
  n_without: number;
  n_with: number;
  weight: number;
  small: boolean;
  stats: BeyondStat[];
  flags: string[];
  note: string;
  source: string;
}

export interface MateOut {
  player_id: number;
  name: string;
  status: string;
  reason: string | null;
  avg_minutes: number;
  without: Per36;
  with_: Per36;
  beyond: Beyond;
  note: string;
}

export interface PlayerRole {
  steadiness: Steadiness;
  onoff: OnOff;
  teammates_out: MateOut[];
}
