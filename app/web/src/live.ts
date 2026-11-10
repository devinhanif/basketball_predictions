// Live play-by-play: wire types, the delta reducer, honest ages, the pace rule and prop trackers.
// Pure functions only (the stream itself is in useLive.ts). No price, odds or edge appears anywhere in the live view.
import type { PSource, PropView, Stat } from "./types";
import { MODEL_LOCK_FROM } from "./record";

export const ATTRIBUTION = "Play-by-play: ESPN. Not affiliated.";
export const CHECKPOINT_LABEL = "UNPROVEN: no live record; most nights the market already knows this.";

export type GameState = "pre" | "in" | "post";

export interface LiveTeam {
  abbr: string;
  name: string;
  score: number;
}
export interface LiveStatus {
  period: number;
  clock: string;
  detail: string;
  elapsed_min: number | null;
}
export interface LiveMeta {
  source: "capture" | "fresh";
  fetched_at: string;
  last_play_at: string | null;
  play_lag_s: number | null;
  server_now: string;
  age_s: number;
}
/** One play: n sequence, p period, c clock, t text, h/a score after it, tm team, s scoring, k kind. */
export interface Play {
  n: number;
  p: number;
  c: string;
  t: string;
  h: number;
  a: number;
  tm: string | null;
  s: 0 | 1;
  k: string;
}
export interface BoxRow {
  id: string;
  name: string;
  team: string;
  starter: boolean;
  min: number | null;
  pts: number;
  reb: number;
  ast: number;
  fg3m: number;
  stl: number;
  blk: number;
  tov: number;
  pf: number;
  fg: string;
  fg3: string;
  ft: string;
  pm: string;
  dnp?: string;
}

export interface LiveGameSummary {
  game_id: string;
  state: GameState;
  tip_utc: string | null;
  away: LiveTeam;
  home: LiveTeam;
  status: LiveStatus;
  n_plays: number;
  source: string;
  fetched_at: string;
  last_play_at: string | null;
  play_lag_s: number | null;
  server_now: string;
  age_s: number;
}
export interface LiveList {
  date: string;
  games: LiveGameSummary[];
  server_now: string;
  attribution: string;
  delay_note: string;
  note?: string;
}

export interface Snapshot {
  seq: number;
  game_id: string;
  date: string;
  state: GameState;
  tip_utc: string | null;
  away: LiveTeam;
  home: LiveTeam;
  status: LiveStatus;
  box: { away: BoxRow[]; home: BoxRow[] };
  plays: Play[];
  n_plays: number;
  first_n: number | null;
  meta: LiveMeta;
  attribution: string;
  delay_note: string;
}
export interface Delta {
  seq: number;
  game_id: string;
  state: GameState;
  away?: LiveTeam;
  home?: LiveTeam;
  status?: LiveStatus;
  plays?: Play[];
  n_plays?: number;
  box?: BoxRow[];
  meta: LiveMeta;
  changed: boolean;
}

/** What the screen holds: the snapshot, folded forward by each delta. Plays ascending by n. */
export interface LiveState {
  seq: number;
  gameId: string;
  date: string;
  state: GameState;
  tipUtc: string | null;
  away: LiveTeam;
  home: LiveTeam;
  status: LiveStatus;
  plays: Play[];
  nPlays: number;
  box: Record<string, BoxRow>;
  meta: LiveMeta;
}

export function fromSnapshot(s: Snapshot): LiveState {
  const box: Record<string, BoxRow> = {};
  for (const r of [...s.box.away, ...s.box.home]) box[r.id] = r;
  return {
    seq: s.seq,
    gameId: s.game_id,
    date: s.date,
    state: s.state,
    tipUtc: s.tip_utc,
    away: s.away,
    home: s.home,
    status: s.status,
    plays: [...s.plays].sort((a, b) => a.n - b.n),
    nPlays: s.n_plays,
    box,
    meta: s.meta,
  };
}

/** Upsert plays by n, keep ascending. New plays are almost always appended, so that path is O(new). */
export function mergePlays(have: Play[], incoming: Play[]): Play[] {
  if (incoming.length === 0) return have;
  const last = have.length > 0 ? (have[have.length - 1] as Play).n : -1;
  if (incoming.every((p) => p.n > last)) return [...have, ...incoming];
  const byN = new Map<number, Play>(have.map((p) => [p.n, p]));
  for (const p of incoming) byN.set(p.n, p);
  return [...byN.values()].sort((a, b) => a.n - b.n);
}

export function applyDelta(s: LiveState, d: Delta): LiveState {
  let box = s.box;
  if (d.box && d.box.length > 0) {
    box = { ...s.box };
    for (const r of d.box) box[r.id] = r;
  }
  return {
    ...s,
    seq: d.seq,
    state: d.state,
    away: d.away ?? s.away,
    home: d.home ?? s.home,
    status: d.status ?? s.status,
    plays: d.plays ? mergePlays(s.plays, d.plays) : s.plays,
    nPlays: d.n_plays ?? s.nPlays,
    box,
    meta: d.meta,
  };
}

/** A delta is applied only if it follows the state we hold; otherwise the caller reconnects for a snapshot. */
export function followsOn(s: LiveState, d: Delta): boolean {
  return d.seq === s.seq + 1;
}

// ---------------------------------------------------------------------------------------------- honest ages

/** Seconds since the body was fetched: the server's own age at send time plus the time since we received it. */
export function ageNow(meta: Pick<LiveMeta, "age_s">, receivedMs: number, nowMs: number): number {
  return Math.max(0, meta.age_s + (nowMs - receivedMs) / 1000);
}

export function ageText(s: number): string {
  const n = Math.round(s);
  if (n < 90) return `${n.toString()} s`;
  const m = Math.floor(n / 60);
  return `${m.toString()} min ${(n % 60).toString()} s`;
}

/** The capture polls about every 60 s; a body older than this while the game is on means the feed is stuck. */
export const STALE_AFTER_S = 150;

export function lagText(lag: number | null): string | null {
  if (lag === null) return null;
  return `The newest play was ${ageText(Math.max(0, lag))} old when we fetched it.`;
}

export function clockLabel(st: LiveStatus, state: GameState): string {
  if (state === "pre") return "Not started";
  if (state === "post") return "Final";
  return st.detail || `Q${st.period.toString()} ${st.clock}`;
}

// ---------------------------------------------------------------------------------------------- pace and trackers

/** Where a line's pace sits at a given game minute. The existing rule is half the line by the half (24 of 48 minutes);
 *  this is that rule held to a straight line, so it is exactly half at the half. */
export function expectedBy(line: number, elapsedMin: number | null): number | null {
  if (elapsedMin === null) return null;
  return line * Math.min(1, Math.max(0, elapsedMin) / 48);
}

export type Pace = "cleared" | "on pace" | "behind" | "not started";

/** An over of `line` needs more than the line: floor(line) + 1 in total. */
export function needed(line: number, cur: number): number {
  return Math.max(0, Math.floor(line) + 1 - cur);
}

export function paceOf(line: number, cur: number, elapsedMin: number | null): Pace {
  if (cur > line) return "cleared";
  const want = expectedBy(line, elapsedMin);
  if (want === null || elapsedMin === 0) return "not started";
  return cur >= want ? "on pace" : "behind";
}

const SUFFIX = /\b(jr|sr|ii|iii|iv|v)\b\.?/g;
export function normName(n: string): string {
  return n
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[.'’-]/g, " ")
    .replace(SUFFIX, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Find a player's box row by name inside one team's rows: exact normalised name, else a unique last name + first initial. */
export function matchRow(name: string, rows: BoxRow[]): BoxRow | null {
  const want = normName(name);
  const exact = rows.filter((r) => normName(r.name) === want);
  if (exact.length === 1) return exact[0] as BoxRow;
  const parts = want.split(" ");
  const last = parts[parts.length - 1] ?? "";
  const first = (parts[0] ?? "").slice(0, 1);
  const loose = rows.filter((r) => {
    const p = normName(r.name).split(" ");
    return (p[p.length - 1] ?? "") === last && (p[0] ?? "").slice(0, 1) === first;
  });
  return loose.length === 1 ? (loose[0] as BoxRow) : null;
}

export const TRACK_STATS: readonly Stat[] = ["pts", "reb", "ast", "fg3m"];

export interface TrackerInput {
  name: string;
  side: "home" | "away";
  props: Partial<Record<Stat, PropView>>;
}
export interface Tracker {
  key: string;
  player: string;
  team: string;
  stat: Stat;
  line: number;
  cur: number | null;
  need: number | null;
  pace: Pace | "no box";
  expected: number | null;
  minutes: number | null;
  fouls: number | null;
  /** The calibrated pre-game chance of the over, or null: model-only numbers are never shown here, and on a night
   *  under the LINE_CAL_LIVE lock no chance is shown at all. */
  chance: number | null;
  /** Where `chance` came from (line_cal or rung_cal), for the label; null when there is none. */
  source: PSource | null;
  extrapolated: boolean;
}

/** The lines on a game against the live box. Posted lines only; a reference line is not a line anyone set. */
export function buildTrackers(players: TrackerInput[], live: LiveState): Tracker[] {
  const showChance = !chanceLocked(live.date);
  const rows = Object.values(live.box);
  const out: Tracker[] = [];
  for (const p of players) {
    const abbr = p.side === "home" ? live.home.abbr : live.away.abbr;
    const row = matchRow(
      p.name,
      rows.filter((r) => r.team === abbr),
    );
    for (const stat of TRACK_STATS) {
      const prop = p.props[stat];
      if (!prop || prop.line_source !== "posted") continue;
      const cur = row ? row[stat] : null;
      out.push({
        key: `${p.name}|${stat}`,
        player: p.name,
        team: abbr,
        stat,
        line: prop.line,
        cur,
        need: cur === null ? null : needed(prop.line, cur),
        pace: cur === null ? "no box" : paceOf(prop.line, cur, live.status.elapsed_min),
        expected: expectedBy(prop.line, live.status.elapsed_min),
        minutes: row ? row.min : null,
        fouls: row ? row.pf : null,
        chance: showChance && prop.model_p_source !== "model" ? prop.model_p_over : null,
        source: showChance && prop.model_p_source !== "model" ? prop.model_p_source : null,
        extrapolated: prop.extrapolated,
      });
    }
  }
  return out;
}

/** On a night under the LINE_CAL_LIVE lock (from MODEL_LOCK_FROM) trackers carry progress and pace only: no pre-game
 *  chance, in progress or final, so a chance never shares a screen with a count that has already decided the prop. */
export function chanceLocked(date: string): boolean {
  return date >= MODEL_LOCK_FROM;
}

/** Hours after tip at which a game is treated as over even if no final body was captured. */
export const OVER_AFTER_H = 4;

/** Final, or started so long ago that it must be (the capture can stop before ESPN flips to final). */
export function treatAsFinal(state: GameState, tipUtc: string | null, nowMs: number): boolean {
  if (state === "post") return true;
  if (state === "pre" || tipUtc === null) return false;
  const tip = Date.parse(tipUtc);
  return Number.isFinite(tip) && nowMs - tip > OVER_AFTER_H * 3600 * 1000;
}

/** Sort: closest to going over first, lines already cleared last, then by player. */
const rank = (t: Tracker): number => (t.pace === "cleared" ? 1000 : t.need ?? 500);

export function sortTrackers(ts: Tracker[]): Tracker[] {
  return [...ts].sort((a, b) => rank(a) - rank(b) || a.player.localeCompare(b.player) || a.stat.localeCompare(b.stat));
}

// ---------------------------------------------------------------------------------------------- slate matching

/** Today's slate date in Eastern time: the capture files a night under its slate date. */
export function etDate(d: Date): string {
  const p = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  return p;
}

export function previousDate(date: string): string {
  const t = new Date(`${date}T12:00:00Z`);
  t.setUTCDate(t.getUTCDate() - 1);
  return t.toISOString().slice(0, 10);
}

/** The live game that is the app's game: same night, same two teams. */
export function findLive(list: LiveGameSummary[], homeAbbr: string, awayAbbr: string): LiveGameSummary | null {
  return list.find((g) => g.home.abbr === homeAbbr && g.away.abbr === awayAbbr) ?? null;
}
