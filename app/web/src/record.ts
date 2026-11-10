// The guess game's running score (you against the outcomes), kept on this device only. try/catch storage through prefs.ts.
import { readKey, writeKey } from "./prefs";
import { wilson } from "./stats";

export type Guess = "over" | "under";

/** Props on nights dated on or after this day sit inside the LINE_CAL_LIVE window (frozen rule): until its
 * single look (about Dec 1) the app shows no model chance or call beside their outcomes. Must equal
 * MODEL_LOCK_FROM in api/trackrecord.py. */
export const MODEL_LOCK_FROM = "2026-10-30";
export const LOCK_NOTE = "The model's number for this night is part of a locked test until about Dec 1.";
export const RESULT_HIDDEN =
  "Result hidden while the model's number for this night is a locked test (about Dec 1). See it in Replay.";
export function isLockedNight(date: string): boolean {
  return date >= MODEL_LOCK_FROM;
}

export interface GuessRound {
  prop_id: string;
  guess: Guess;
  went_over: boolean;
  you_right: boolean;
}

export const GUESS_KEY = "ph.guess.v1";
const KEEP = 500;

export function isRound(x: unknown): x is GuessRound {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    typeof r["prop_id"] === "string" &&
    (r["guess"] === "over" || r["guess"] === "under") &&
    typeof r["went_over"] === "boolean" &&
    typeof r["you_right"] === "boolean"
  );
}

/** What this device has played so far; anything unreadable counts as nothing played. */
export function loadRounds(): GuessRound[] {
  try {
    const raw = readKey(GUESS_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isRound).slice(-KEEP) : [];
  } catch {
    return [];
  }
}

export function saveRounds(rounds: readonly GuessRound[]): void {
  writeKey(GUESS_KEY, JSON.stringify(rounds.slice(-KEEP)));
}

export function makeRound(propId: string, guess: Guess, wentOver: boolean): GuessRound {
  const truth: Guess = wentOver ? "over" : "under";
  return { prop_id: propId, guess, went_over: wentOver, you_right: guess === truth };
}

export interface Score {
  n: number;
  you: number;
  youRange: [number, number] | null;
}

/** Counts, and a 95% range for your hit rate once there are 10 rounds (before that, "not enough to say"). */
export function scoreOf(rounds: readonly GuessRound[]): Score {
  const n = rounds.length;
  const you = rounds.filter((r) => r.you_right).length;
  return {
    n,
    you,
    youRange: n >= 10 ? wilson(you, n) : null,
  };
}

export function newSeed(): number {
  return 1 + Math.floor(Math.random() * 1_000_000);
}
