import raw from "./fixtures/studio_builds.json";

export interface Bar {
  stat: string;
  per36: number;
  percentile: number;
  n_games: number;
  n_minutes: number;
}
export interface ZoneLine {
  zone: string;
  att: number;
  made: number;
  league_att: number;
  league_made: number;
}
export interface Potential {
  metric: string;
  p10: number;
  p90: number;
  n_analogs: number;
  rule: string;
}
export interface Borrowed {
  rule: string;
  stand_in_for: string;
  n_peers: number;
  per36: Record<string, number>;
}
export interface BuildCard {
  player_id: number;
  name: string;
  team: string;
  position_listed: string | null;
  class: string | null;
  class_note: string;
  level: number;
  minutes_share: number;
  starter_rate: number;
  n_games_window: number;
  n_games_this_season: number;
  age: number | null;
  draft_year: number | null;
  draft_pick: number | null;
  draft_status: string | null;
  first_season: number | null;
  seasons_played: number | null;
  minutes_trend: { recent_mpg: number; recent_n: number; prior_mpg: number | null; prior_n: number };
  potential: Potential | null;
  bars: Bar[];
  zones: ZoneLine[];
  borrowed: Borrowed | null;
}
export interface BuildMeta {
  as_of: string;
  window: string;
  sources: Record<string, string>;
  level_cut_points_minutes_share: number[];
  level_rule: string;
  note: string;
}

export const BUILDS = raw as unknown as { meta: BuildMeta; cards: BuildCard[] };

export const STAT_NAME: Record<string, string> = {
  pts: "Points",
  reb: "Rebounds",
  ast: "Assists",
  fg3m: "Threes made",
  stl: "Steals",
  blk: "Blocks",
  tov: "Turnovers (fewer is better)",
};

/** Level from the committed cut points: 1 + how many cut points the minutes share exceeds. */
export function levelOf(minutesShare: number, cuts: number[]): number {
  return 1 + cuts.filter((c) => minutesShare > c).length;
}

/** Ordinal for a percentile, never claiming 100th or 0th from a sample. */
export function ordinal(n: number): string {
  const v = Math.min(99, Math.max(1, Math.round(n)));
  const s = ["th", "st", "nd", "rd"];
  const m = v % 100;
  return `${v.toString()}${s[(m - 20) % 10] ?? s[m] ?? s[0] ?? "th"}`;
}

export function zonePct(z: ZoneLine): number | null {
  return z.att > 0 ? z.made / z.att : null;
}
export function leaguePct(z: ZoneLine): number | null {
  return z.league_att > 0 ? z.league_made / z.league_att : null;
}

export const THIN_GAMES = 20;
/** The sample is thin when the player has fewer own games than the league-distribution gate. */
export function isThin(c: BuildCard): boolean {
  return c.n_games_window < THIN_GAMES;
}

export function potentialText(p: Potential | null): string {
  if (!p) return "Not enough comparable players to give a range (needs at least 30).";
  return `${p.p10.toFixed(1)} to ${p.p90.toFixed(1)} minutes a game next season, the middle 80% of ${p.n_analogs.toString()} similar player-seasons`;
}

export function draftText(c: BuildCard): string {
  if (c.draft_pick === null || c.draft_year === null) return c.draft_status === "undrafted" ? "Undrafted" : "Draft position not on file";
  return `Pick ${c.draft_pick.toString()}, ${c.draft_year.toString()} draft`;
}

/** Capitalise the first letter of a stored rule string for display. */
export function cap(t: string): string {
  return t.length === 0 ? t : t.charAt(0).toUpperCase() + t.slice(1);
}
