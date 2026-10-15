import type { ResultRow, Stat } from "./types";
import { MIN_LEGS, wilson } from "./stats";

/** One row per swiped card. Paper only: no stake, no units. Blind by default (saw_model false); opening the forecast panel sets it. */
export interface Pick {
  id: string;
  prop_id: string;
  slate_date: string;
  game_id: string;
  player: string;
  team: string;
  opponent: string;
  stat: Stat;
  line: number;
  over_price: number;
  under_price: number;
  tip_utc: string;
  side: "over" | "under" | "skip";
  /** Real wall-clock time the pick was made. */
  made_at: string;
  /** The clock the tip check used (frozen in replay mode, equal to made_at when live). */
  slate_clock: string;
  response_ms: number;
  source: "swipe";
  /** True when the card's stats or line slider (which show our forecast) were opened before the pick. */
  saw_model: boolean;
}

export const LEDGER_KEY = "ph.ledger.v1";

/** localStorage can throw (private windows, blocked storage); then the ledger lives in memory. */
let memory: Pick[] = [];
let memoryOnly = false;

export function loadLedger(): Pick[] {
  if (memoryOnly) return memory;
  try {
    const raw = localStorage.getItem(LEDGER_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Pick[]) : [];
  } catch {
    memoryOnly = true;
    return memory;
  }
}

function save(rows: Pick[]): void {
  memory = rows;
  try {
    localStorage.setItem(LEDGER_KEY, JSON.stringify(rows));
  } catch {
    memoryOnly = true;
  }
}

/** A pick is refused at or after the game's tip (ISO-8601 UTC strings compare as instants). */
export function canPlace(tipUtc: string, clockUtc: string): boolean {
  return new Date(clockUtc).getTime() < new Date(tipUtc).getTime();
}

export type AddResult = { ok: true; pick: Pick } | { ok: false; reason: "after_tip" | "duplicate" };

export function addPick(
  rows: Pick[],
  card: Omit<Pick, "id" | "made_at" | "slate_clock" | "source" | "saw_model" | "side" | "response_ms">,
  side: Pick["side"],
  clockUtc: string,
  responseMs: number,
  now: Date = new Date(),
  sawModel = false,
): { rows: Pick[]; result: AddResult } {
  if (!canPlace(card.tip_utc, clockUtc)) return { rows, result: { ok: false, reason: "after_tip" } };
  if (rows.some((r) => r.prop_id === card.prop_id)) {
    return { rows, result: { ok: false, reason: "duplicate" } };
  }
  const pick: Pick = {
    ...card,
    id: card.prop_id,
    side,
    made_at: now.toISOString(),
    slate_clock: clockUtc,
    response_ms: Math.max(0, Math.round(responseMs)),
    source: "swipe",
    saw_model: sawModel,
  };
  const next = [...rows, pick];
  save(next);
  return { rows: next, result: { ok: true, pick } };
}

export function clearLedger(): void {
  save([]);
}

export type Outcome = "win" | "loss" | "push";

export function settleSide(side: "over" | "under", line: number, actual: number): Outcome {
  if (actual === line) return "push";
  const overHit = actual > line;
  return (side === "over") === overHit ? "win" : "loss";
}

export interface Settled {
  pick: Pick;
  row: ResultRow;
  outcome: Outcome;
  /** Model P(chosen side) given no push, and the book's de-vigged equivalent. */
  model_side: number;
  market_side: number;
}

export function settle(picks: Pick[], rows: ResultRow[]): { settled: Settled[]; open: Pick[] } {
  const byId = new Map(rows.map((r) => [r.prop_id, r]));
  const settled: Settled[] = [];
  const open: Pick[] = [];
  for (const p of picks) {
    if (p.side === "skip") continue;
    const row = byId.get(p.prop_id);
    if (!row) {
      open.push(p);
      continue;
    }
    const over = p.side === "over";
    const pm = row.model_p_over + row.model_p_under;
    const modelOver = pm > 0 ? row.model_p_over / pm : 0.5;
    settled.push({
      pick: p,
      row,
      outcome: settleSide(p.side, row.line, row.actual),
      model_side: over ? modelOver : 1 - modelOver,
      market_side: over ? row.market_p_over : row.market_p_under,
    });
  }
  return { settled, open };
}

export interface Bin {
  label: string;
  n: number;
  predicted: number | null;
  observed: number | null;
  ci: [number, number] | null;
  enough: boolean;
}

const EDGES: [number, number, string][] = [
  [0, 0.4, "under 40%"],
  [0.4, 0.5, "40% to 50%"],
  [0.5, 0.6, "50% to 60%"],
  [0.6, 1.0001, "60% and over"],
];

export interface Summary {
  n_picks: number;
  n_skips: number;
  wins: number;
  losses: number;
  pushes: number;
  decided: number;
  hit_rate: number | null;
  hit_ci: [number, number] | null;
  mean_market: number | null;
  mean_model: number | null;
  enough: boolean;
  bins: Bin[];
}

export function summarize(all: Pick[], settled: Settled[]): Summary {
  const decided = settled.filter((s) => s.outcome !== "push");
  const wins = decided.filter((s) => s.outcome === "win").length;
  const n = decided.length;
  const mean = (xs: number[]): number | null =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const bins: Bin[] = EDGES.map(([lo, hi, label]) => {
    const inBin = decided.filter((s) => s.model_side >= lo && s.model_side < hi);
    const k = inBin.filter((s) => s.outcome === "win").length;
    const enough = inBin.length >= MIN_LEGS;
    return {
      label,
      n: inBin.length,
      predicted: mean(inBin.map((s) => s.model_side)),
      observed: inBin.length ? k / inBin.length : null,
      ci: inBin.length ? wilson(k, inBin.length) : null,
      enough,
    };
  });
  return {
    n_picks: all.filter((p) => p.side !== "skip").length,
    n_skips: all.filter((p) => p.side === "skip").length,
    wins,
    losses: n - wins,
    pushes: settled.length - n,
    decided: n,
    hit_rate: n ? wins / n : null,
    hit_ci: n ? wilson(wins, n) : null,
    mean_market: mean(decided.map((s) => s.market_side)),
    mean_model: mean(decided.map((s) => s.model_side)),
    enough: n >= MIN_LEGS,
    bins,
  };
}

export type Streak = { kind: "win" | "loss"; count: number } | null;

function byMadeAt(a: Settled, b: Settled): number {
  return a.pick.made_at < b.pick.made_at ? -1 : a.pick.made_at > b.pick.made_at ? 1 : 0;
}

/** The run at the end of the ledger, oldest to newest by made_at. Pushes neither extend nor break it. */
export function streak(settled: Settled[]): Streak {
  const ordered = settled.filter((s) => s.outcome !== "push").sort(byMadeAt);
  const last = ordered[ordered.length - 1];
  if (!last || last.outcome === "push") return null;
  let count = 0;
  for (let i = ordered.length - 1; i >= 0; i--) {
    if (ordered[i]?.outcome !== last.outcome) break;
    count++;
  }
  return { kind: last.outcome, count };
}

export interface WinLoss {
  wins: number;
  losses: number;
}

export function recordOf(settled: Settled[]): WinLoss {
  const decided = settled.filter((s) => s.outcome !== "push");
  const wins = decided.filter((s) => s.outcome === "win").length;
  return { wins, losses: decided.length - wins };
}

/** Picks made in the 7 days before `now` (by made_at, real time). */
export function lastWeek(settled: Settled[], now: Date = new Date()): Settled[] {
  const cutoff = now.getTime() - 7 * 24 * 3600 * 1000;
  return settled.filter((s) => new Date(s.pick.made_at).getTime() >= cutoff);
}
