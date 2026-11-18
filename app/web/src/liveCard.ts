// The live score card: wire types, the one-sentence pace line, sorting, and "what changed since the last update".
// Pure functions only (the screens are in LiveCardParts.tsx). Every number printed here is a field of the payload or
// plain arithmetic on two of them (count so far against the line, the count still needed, game time left); nothing here
// is a probability, and no sentence calls anything likely or unlikely.
import type { LiveStatus, LiveTeam, GameState } from "./live";

export type CardStat = "pts" | "reb" | "ast" | "fg3m";
export type RowStatus = "playing" | "not_entered" | "dnp" | "fouled_out" | "no_box" | "pregame";

export interface InGame {
  chance: number;
  label: string;
  known_at: string;
  age_s: number;
  as_of: string;
}
export interface CardPrice {
  over: number;
  under: number;
  book: string | null;
  snapshot_utc: string | null;
}
export interface CardRow {
  key: string;
  player_id: number;
  name: string;
  team: string;
  side: "home" | "away";
  stat: CardStat;
  line: number;
  cur: number | null;
  need: number | null;
  cleared: boolean;
  minutes: number | null;
  fouls: number | null;
  starter: boolean;
  status: RowStatus;
  reason: string | null;
  /** The calibrated pre-game chance of the over, or null (never a raw number; the server also withholds it under the lock). */
  chance: number | null;
  chance_source: "line_cal" | "rung_cal" | null;
  price: CardPrice | null;
  in_game?: InGame;
}
export interface LiveCard {
  game_id: string;
  date: string;
  state: GameState;
  tip_utc: string | null;
  away: LiveTeam;
  home: LiveTeam;
  status: LiveStatus;
  as_of: { label: string | null; source: string; fetched_at: string; server_now: string; age_s: number };
  feed_note: string;
  regulation_left_min: number | null;
  overtime: boolean;
  lines_state: "ok" | "loading" | "unavailable" | "no_stored_game";
  chance_locked: boolean;
  tracked: { rows: number; players: number; reference_only: number; text: string };
  model_on: boolean;
  rows: CardRow[];
}
export interface LiveCardList {
  date: string;
  server_now: string;
  games: LiveCard[];
  note?: string;
}

/** A row with the game it belongs to, for the slate view. */
export interface SlateRow {
  row: CardRow;
  card: LiveCard;
}

export const STAT_WORD: Record<CardStat, string> = { pts: "points", reb: "rebounds", ast: "assists", fg3m: "threes" };
export const STAT_CAP: Record<CardStat, string> = { pts: "PTS", reb: "REB", ast: "AST", fg3m: "3PM" };

const num = (x: number): string => (Number.isInteger(x) ? x.toString() : x.toFixed(1).replace(/\.0$/, ""));

/** "about 14 minutes" of game time left, from the game clock only. */
export function minutesText(left: number): string {
  if (left < 1) return "less than a minute";
  const m = Math.round(left);
  return `about ${m.toString()} minute${m === 1 ? "" : "s"}`;
}

export function gameLabel(c: Pick<LiveCard, "away" | "home">): string {
  return `${c.away.abbr} at ${c.home.abbr}`;
}

/** Is regulation over while the game is still going (overtime, or the buzzer has gone and the feed has not caught up)? */
export function regulationOver(c: Pick<LiveCard, "state" | "regulation_left_min" | "overtime">): boolean {
  return c.state === "in" && (c.overtime || c.regulation_left_min === 0);
}

/**
 * The one plain sentence for a row. Arithmetic on displayed facts: the count so far, the line, the count still needed
 * and the game time left by the clock. It is not a chance and it never says likely or unlikely.
 */
export function paceSentence(r: CardRow, c: Pick<LiveCard, "state" | "regulation_left_min" | "overtime" | "status">): string {
  const w = STAT_WORD[r.stat];
  const line = num(r.line);
  if (r.status === "dnp") return `Did not play${r.reason && r.reason !== "did not play" ? ` (${r.reason})` : ""}. Line ${line} ${w}.`;
  if (r.status === "no_box") {
    return c.state === "pre" ? `Line ${line} ${w}. Game not started.` : `Not in the live box score. Line ${line} ${w}.`;
  }
  if (c.state === "pre" || r.status === "pregame") return `Line ${line} ${w}. Game not started.`;
  const cur = r.cur ?? 0;
  const tail = r.status === "fouled_out" ? " Fouled out." : "";
  if (c.state === "post") {
    return r.cleared ? `Final: over, ${num(cur)} against a line of ${line} ${w}.${tail}` : `Final: ${num(cur)}, line ${line} ${w}. Under the line.${tail}`;
  }
  if (r.cleared) return `Over: ${num(cur)}, line ${line} ${w}.${tail}`;
  if (r.status === "not_entered") return `Has not entered the game yet. Line ${line} ${w}.`;
  const need = r.need ?? 0;
  if (c.overtime) return `Overtime. ${num(cur)} of ${line} ${w}. Needs ${need.toString()} more.${tail}`;
  if (c.regulation_left_min === 0) return `Not reached in regulation: ${num(cur)} of ${line} ${w}.${tail}`;
  if (c.regulation_left_min === null) return `${num(cur)} of ${line} ${w}. Needs ${need.toString()} more.${tail}`;
  return `${num(cur)} of ${line} ${w}. Needs ${need.toString()} more in ${minutesText(c.regulation_left_min)} of game time left.${tail}`;
}

/** Bar geometry in percent of the track: the fill (count) and the tick (line), on one scale that leaves room past the line. */
export function bar(line: number, cur: number | null): { fill: number; tick: number } {
  const c = cur ?? 0;
  const top = Math.max(line * 1.25, c, 1);
  return { fill: Math.min(100, (c / top) * 100), tick: Math.min(100, (line / top) * 100) };
}

/** The word beside the count that says where the row stands, so colour is never the only signal. */
export function standingWord(r: CardRow, state: GameState): string {
  if (r.status === "dnp") return "DID NOT PLAY";
  if (r.status === "no_box") return state === "pre" ? "NOT STARTED" : "NOT IN BOX";
  if (state === "pre" || r.status === "pregame") return "NOT STARTED";
  if (r.cleared) return state === "post" ? "OVER" : "OVER NOW";
  if (state === "post") return "UNDER";
  if (r.status === "not_entered") return "NOT ENTERED";
  if (r.status === "fouled_out") return "FOULED OUT";
  return "IN PLAY";
}

// ------------------------------------------------------------------------------------------ sorting

export type SortKey = "closest" | "game" | "changed";
export const SORT_LABEL: Record<SortKey, string> = { closest: "Closest to the line", game: "Game", changed: "Recently changed" };

/** Closest first: in play and not yet over by the share of the line still to go; then rows already over; then rows that
 *  have not started; then the rest (did not play, final). The share is a sort key only; it is never shown as a chance. */
function closeness(r: CardRow, state: GameState): number {
  if (state === "post" || r.status === "dnp") return 4000;
  if (r.status === "no_box" || r.status === "pregame" || state === "pre") return 3000;
  if (r.cleared) return 2000;
  if (r.status === "not_entered") return 2500;
  const need = r.need ?? Number.MAX_SAFE_INTEGER;
  return need / Math.max(1, Math.floor(r.line) + 1);
}

export function sortSlate(items: SlateRow[], key: SortKey, changedAt: Readonly<Record<string, number>> = {}): SlateRow[] {
  const byName = (a: SlateRow, b: SlateRow): number => a.row.name.localeCompare(b.row.name) || a.row.stat.localeCompare(b.row.stat);
  const out = [...items];
  if (key === "game") {
    return out.sort((a, b) => a.card.game_id.localeCompare(b.card.game_id) || closeness(a.row, a.card.state) - closeness(b.row, b.card.state) || byName(a, b));
  }
  if (key === "changed") {
    const at = (s: SlateRow): number => changedAt[`${s.card.game_id}|${s.row.key}`] ?? 0;
    return out.sort((a, b) => at(b) - at(a) || closeness(a.row, a.card.state) - closeness(b.row, b.card.state) || byName(a, b));
  }
  return out.sort((a, b) => closeness(a.row, a.card.state) - closeness(b.row, b.card.state) || byName(a, b));
}

// ------------------------------------------------------------------------------------------ what changed

/** How long a changed row stays marked. */
export const CHANGED_FOR_MS = 8000;

export interface ChangeBook {
  /** The count each row had at the last update (null when it had none). */
  last: Record<string, number | null>;
  /** When each row's count last moved (ms, the caller's clock). */
  at: Record<string, number>;
  /** By how much it moved. */
  by: Record<string, number>;
}
export const emptyBook = (): ChangeBook => ({ last: {}, at: {}, by: {} });

/**
 * Compare the new rows with the last update and mark the rows whose count moved. The first update marks nothing (there is
 * nothing to compare with). Keys are game-qualified so one book serves the slate view.
 */
export function noteChanges(book: ChangeBook, rows: { key: string; cur: number | null }[], now: number): ChangeBook {
  const first = Object.keys(book.last).length === 0;
  const last: Record<string, number | null> = {};
  const at = { ...book.at };
  const by = { ...book.by };
  for (const r of rows) {
    last[r.key] = r.cur;
    const before = book.last[r.key];
    if (!first && before !== undefined && r.cur !== null && before !== null && r.cur !== before) {
      at[r.key] = now;
      by[r.key] = r.cur - before;
    }
  }
  return { last, at, by };
}

export function isRecent(book: ChangeBook, key: string, now: number): boolean {
  const t = book.at[key];
  return t !== undefined && now - t < CHANGED_FOR_MS;
}

// ------------------------------------------------------------------------------------------ freshness

/** "3 s ago" for a young copy, "about 1 min ago" after that. */
export function agoText(ageS: number): string {
  const s = Math.max(0, Math.round(ageS));
  if (s < 45) return `${s.toString()} s ago`;
  return `about ${Math.max(1, Math.round(s / 60)).toString()} min ago`;
}

/** "as of Q3 4:12, about 1 min ago" (a game not in play has no clock, so it says "as of the last copy"). */
export function asOfText(c: Pick<LiveCard, "state" | "as_of">, ageS: number): string {
  if (c.state === "post") return "Game over. This is the last copy captured.";
  if (c.state === "pre") return `Not started. Last copy ${agoText(ageS)}.`;
  return `as of ${c.as_of.label ?? "the game clock"}, ${agoText(ageS)}`;
}

export function playerWord(n: number): string {
  return `${n.toString()} ${n === 1 ? "player" : "players"}`;
}

/** The step-2 line, built only from the payload: the exact label, the model's chance, and how old it is. */
export function inGameText(m: InGame): string {
  return `${m.label}: ${Math.round(m.chance * 100).toString()}% (model as of ${m.as_of}, ${agoText(m.age_s)})`;
}
