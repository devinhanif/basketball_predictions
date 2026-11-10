// The guess game's running score (you against the model), kept on this device only. try/catch storage through prefs.ts.
import { readKey, writeKey } from "./prefs";
import { wilson } from "./stats";

export type Guess = "over" | "under";

export interface GuessRound {
  prop_id: string;
  guess: Guess;
  model_call: Guess;
  went_over: boolean;
  you_right: boolean;
  model_right: boolean;
}

export const GUESS_KEY = "ph.guess.v1";
const KEEP = 500;

export function isRound(x: unknown): x is GuessRound {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    typeof r["prop_id"] === "string" &&
    (r["guess"] === "over" || r["guess"] === "under") &&
    (r["model_call"] === "over" || r["model_call"] === "under") &&
    typeof r["went_over"] === "boolean" &&
    typeof r["you_right"] === "boolean" &&
    typeof r["model_right"] === "boolean"
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

export function makeRound(propId: string, guess: Guess, modelCall: Guess, wentOver: boolean): GuessRound {
  const truth: Guess = wentOver ? "over" : "under";
  return { prop_id: propId, guess, model_call: modelCall, went_over: wentOver, you_right: guess === truth, model_right: modelCall === truth };
}

export interface Score {
  n: number;
  you: number;
  model: number;
  both: number;
  onlyYou: number;
  onlyModel: number;
  neither: number;
  youRange: [number, number] | null;
  modelRange: [number, number] | null;
}

/** Counts, and a 95% range for each side's hit rate once there are 10 rounds (before that, "not enough to say"). */
export function scoreOf(rounds: readonly GuessRound[]): Score {
  const n = rounds.length;
  const you = rounds.filter((r) => r.you_right).length;
  const model = rounds.filter((r) => r.model_right).length;
  const both = rounds.filter((r) => r.you_right && r.model_right).length;
  return {
    n,
    you,
    model,
    both,
    onlyYou: you - both,
    onlyModel: model - both,
    neither: n - you - model + both,
    youRange: n >= 10 ? wilson(you, n) : null,
    modelRange: n >= 10 ? wilson(model, n) : null,
  };
}

export function newSeed(): number {
  return 1 + Math.floor(Math.random() * 1_000_000);
}
