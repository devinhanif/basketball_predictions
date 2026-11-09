// The Jarvis Desk export (docs/contracts/desk_export_v1.md) as the screens use it, and the pure helpers over it.
// Every number is a stored field. A null field means "not available" and the screens say so in words.
import { wilson } from "./stats";

export interface DeskResult {
  status: string;
  won: boolean | null;
  close_cents: number | null;
  clv_cents: number | null;
  pnl_usd: number | null;
}
export interface DeskPlay {
  play_id: string;
  slate_date: string;
  ts: string;
  game_id: string | null;
  matchup: string | null;
  tipoff: string | null;
  kind: string;
  side: string;
  player_id: number | null;
  player_name: string | null;
  team: string | null;
  stat: string | null;
  threshold: number | null;
  leg_desc: string | null;
  price_cents: number | null;
  fee_cents: number | null;
  fair_prob: number | null;
  model_prob: number | null;
  edge_cents: number | null;
  edge_label: string | null;
  reason: string | null;
  contracts: number | null;
  stake_usd: number | null;
  result: DeskResult | null;
}
export type Certainty = "reported" | "inference" | "correction";
export interface DeskObservation {
  id: string;
  slate_date: string;
  ts: string;
  game_id: string | null;
  matchup: string | null;
  when_text: string | null;
  kind: string;
  title: string;
  text: string | null;
  certainty: Certainty;
  corrects: string | null;
}
export interface DeskAlert {
  id: string;
  ts: string;
  slate_date: string;
  kind: string;
  mode: string | null;
  title: string;
  text: string | null;
  play_ids: string[];
  observation_ids: string[];
}
export interface DeskHalftime {
  slate_date: string;
  game_id: string;
  matchup: string | null;
  player_id: number | null;
  player_name: string | null;
  stat: string;
  threshold: number;
  price_cents: number | null;
  model_prob: number | null;
  edge_cents: number | null;
  result: string | null;
}
export interface CalBin {
  bin_lo: number;
  bin_hi: number;
  n: number;
  mean_pred: number;
  hit_rate: number;
}
export interface Desk {
  schema: 1;
  privacy: "private" | "shared";
  fixture: string | null;
  built_at: string;
  data_as_of: string;
  sources: Record<string, string>;
  status: {
    kill_switch: { on: boolean; since: string | null; n: number | null; mean_clv_cents: number | null; note: string | null };
    model_review: boolean;
    w_model: number | null;
    w_model_by_stat: Record<string, number>;
  };
  /** The producer's reason for every null or empty section; shown verbatim in the empty states. */
  section_notes: Record<string, string>;
  balance: { start_usd: number; current_usd: number; floor_usd: number } | null;
  plays: DeskPlay[];
  clv_summary: {
    n: number;
    mean_cents: number | null;
    ci95: [number, number] | null;
    clustered_on: string;
    reason: string | null;
    n_dates?: number;
  } | null;
  record: { n_settled: number; n_won: number; n_lost: number; n_void: number } | null;
  daily: { slate_date: string; n: number; n_won: number; clv_mean_cents: number | null; pnl_usd: number | null }[];
  observations: DeskObservation[];
  alerts: DeskAlert[];
  halftime: DeskHalftime[];
  track_record: {
    scorecard_date: string | null;
    model_vs_market: {
      n: number;
      metric: string;
      diff: number | null;
      ci95: [number, number] | null;
      clustered_on: string;
      reason: string | null;
      games?: number;
      note?: string;
    } | null;
    calibration: CalBin[];
  } | null;
}

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);
const arr = (x: unknown): unknown[] => (Array.isArray(x) ? x : []);

/** Check a /api/desk answer. Null when it is not a schema 1 desk export (the screens then say "not available"). */
export function parseDesk(x: unknown): Desk | null {
  if (!isObj(x) || x["schema"] !== 1) return null;
  if (x["privacy"] !== "private" && x["privacy"] !== "shared") return null;
  if (typeof x["built_at"] !== "string" || typeof x["data_as_of"] !== "string" || !isObj(x["status"])) return null;
  const st = x["status"];
  const ks = isObj(st["kill_switch"]) ? st["kill_switch"] : {};
  const d: Desk = {
    schema: 1,
    privacy: x["privacy"],
    fixture: typeof x["_fixture"] === "string" ? x["_fixture"] : null,
    built_at: x["built_at"],
    data_as_of: x["data_as_of"],
    sources: isObj(x["sources"]) ? (x["sources"] as Record<string, string>) : {},
    status: {
      kill_switch: {
        on: ks["on"] === true,
        since: typeof ks["since"] === "string" ? ks["since"] : null,
        n: typeof ks["n"] === "number" ? ks["n"] : null,
        mean_clv_cents: typeof ks["mean_clv_cents"] === "number" ? ks["mean_clv_cents"] : null,
        note: typeof ks["note"] === "string" ? ks["note"] : null,
      },
      model_review: st["model_review"] === true,
      w_model: typeof st["w_model"] === "number" ? st["w_model"] : null,
      w_model_by_stat: isObj(st["w_model_by_stat"]) ? (st["w_model_by_stat"] as Record<string, number>) : {},
    },
    section_notes: isObj(x["section_notes"]) ? (x["section_notes"] as Record<string, string>) : {},
    balance: isObj(x["balance"]) ? (x["balance"] as unknown as Desk["balance"]) : null,
    plays: arr(x["plays"]).filter(isObj) as unknown as DeskPlay[],
    clv_summary: isObj(x["clv_summary"]) ? (x["clv_summary"] as unknown as Desk["clv_summary"]) : null,
    record: isObj(x["record"]) ? (x["record"] as unknown as Desk["record"]) : null,
    daily: arr(x["daily"]).filter(isObj) as unknown as Desk["daily"],
    observations: arr(x["observations"]).filter(isObj) as unknown as DeskObservation[],
    alerts: arr(x["alerts"]).filter(isObj) as unknown as DeskAlert[],
    halftime: arr(x["halftime"]).filter(isObj) as unknown as DeskHalftime[],
    track_record: isObj(x["track_record"]) ? (x["track_record"] as unknown as Desk["track_record"]) : null,
  };
  return d;
}

/* ------------------------------------------------------------------ formatting (cents; $ only where a private field is filled) */
export function cents(x: number | null, digits = 0): string {
  if (x === null) return "-";
  const v = Number.isInteger(x) && digits === 0 ? x.toFixed(0) : x.toFixed(Math.max(digits, Number.isInteger(x) ? 0 : 1));
  return `${v}c`;
}
export function signedCents(x: number | null, digits = 1): string {
  if (x === null) return "not stored";
  const r = Math.round(x * 10 ** digits) / 10 ** digits;
  return `${r < 0 ? "-" : "+"}${Math.abs(r).toFixed(digits)}c`;
}
export function usd(x: number): string {
  return `${x < 0 ? "-" : x > 0 ? "+" : ""}$${Math.abs(x).toFixed(2)}`;
}

/* ------------------------------------------------------------------ plays */
export function playLabel(p: DeskPlay): string {
  return p.leg_desc ?? [p.player_name, p.threshold !== null ? `${p.threshold.toString()}+` : null, p.stat].filter(Boolean).join(" ");
}

/** Plays on a slate date, best edge first (a null edge sorts last). */
export function playsOn(desk: Desk, date: string): DeskPlay[] {
  return desk.plays
    .filter((p) => p.slate_date === date)
    .sort((a, b) => (b.edge_cents ?? -Infinity) - (a.edge_cents ?? -Infinity));
}

/** The newest slate date that has plays (tonight's when there are any). */
export function latestPlayDate(desk: Desk): string | null {
  return desk.plays.map((p) => p.slate_date).sort().at(-1) ?? null;
}

export function settledPlays(desk: Desk): DeskPlay[] {
  return desk.plays.filter((p) => p.result !== null && p.result.status !== "open");
}

/* ------------------------------------------------------------------ ledger */
export type LedgerFilter = "all" | "positive" | "negative" | "flat";
export const LEDGER_FILTERS: readonly { value: LedgerFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "positive", label: "Positive" },
  { value: "negative", label: "Negative" },
  { value: "flat", label: "Flat" },
];

/** What the filters sort by: net P&L where it is stored (private cut), else closing-line value (shared cut). */
export function ledgerMetric(p: DeskPlay): number | null {
  return p.result?.pnl_usd ?? p.result?.clv_cents ?? null;
}

export function filterLedger(rows: readonly DeskPlay[], f: LedgerFilter): DeskPlay[] {
  if (f === "all") return [...rows];
  return rows.filter((p) => {
    const m = ledgerMetric(p);
    if (m === null) return false;
    return f === "positive" ? m > 0 : f === "negative" ? m < 0 : m === 0;
  });
}

function csvCell(v: string | number | null | boolean): string {
  if (v === null) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

/** The ledger as CSV. Money columns appear only when the cut holds them. */
export function ledgerCsv(rows: readonly DeskPlay[], withMoney: boolean): string {
  const head = ["slate_date", "matchup", "contract", "side", "entry_cents", "fee_cents", "edge_cents", "close_cents", "clv_cents", "won"];
  if (withMoney) head.push("pnl_usd");
  const lines = [head.join(",")];
  for (const p of rows) {
    const r = p.result;
    const cells: (string | number | null | boolean)[] = [
      p.slate_date, p.matchup, playLabel(p), p.side, p.price_cents, p.fee_cents, p.edge_cents, r?.close_cents ?? null, r?.clv_cents ?? null, r?.won ?? null,
    ];
    if (withMoney) cells.push(r?.pnl_usd ?? null);
    lines.push(cells.map(csvCell).join(","));
  }
  return `${lines.join("\n")}\n`;
}

/* ------------------------------------------------------------------ rates with n and an interval */
export interface Rate {
  n: number;
  value: number | null;
  ci: [number, number] | null;
  /** Why there is no number, in words. */
  reason: string | null;
}

/** Win rate with a Wilson interval; below MIN settled bets it says "not enough to say". */
export function winRate(rec: Desk["record"], min = 20): Rate {
  if (!rec) return { n: 0, value: null, ci: null, reason: "No record is stored." };
  const n = rec.n_won + rec.n_lost;
  if (n === 0) return { n, value: null, ci: null, reason: "No settled bets yet." };
  const value = rec.n_won / n;
  if (n < min) return { n, value, ci: null, reason: `Only ${n.toString()} settled, not enough to say (we want at least ${min.toString()}).` };
  return { n, value, ci: wilson(rec.n_won, n), reason: null };
}

export function clvRate(desk: Desk): Rate {
  const c = desk.clv_summary;
  if (!c) return { n: 0, value: null, ci: null, reason: "No closing-line value is stored." };
  if (c.mean_cents === null || c.ci95 === null) return { n: c.n, value: c.mean_cents, ci: null, reason: c.reason ?? "Not enough to say." };
  return { n: c.n, value: c.mean_cents, ci: c.ci95, reason: null };
}

/* ------------------------------------------------------------------ day bars */
export interface DayBar {
  date: string;
  /** Mean closing-line value that day, in cents; null when none is stored. */
  value: number | null;
  pnl: number | null;
  n: number;
  won: number;
}

export function dayBars(desk: Desk): DayBar[] {
  return [...desk.daily]
    .sort((a, b) => a.slate_date.localeCompare(b.slate_date))
    .map((d) => ({ date: d.slate_date, value: d.clv_mean_cents, pnl: d.pnl_usd, n: d.n, won: d.n_won }));
}

/** Symmetric scale for bars: the largest absolute value, never zero. */
export function barScale(bars: readonly DayBar[]): number {
  return Math.max(0.5, ...bars.map((b) => Math.abs(b.value ?? 0)));
}

export function dayTally(bars: readonly DayBar[]): { up: number; down: number; flat: number; none: number } {
  const t = { up: 0, down: 0, flat: 0, none: 0 };
  for (const b of bars) {
    if (b.value === null) t.none++;
    else if (b.value > 0) t.up++;
    else if (b.value < 0) t.down++;
    else t.flat++;
  }
  return t;
}

/* ------------------------------------------------------------------ calibration */
/** Reliability points, or null (with the reason) while there are no bins to draw. */
export function calibration(desk: Desk): { bins: CalBin[]; total: number } | null {
  const bins = (desk.track_record?.calibration ?? []).filter(
    (b) => [b.bin_lo, b.bin_hi, b.n, b.mean_pred, b.hit_rate].every((v) => typeof v === "number" && Number.isFinite(v)) && b.n > 0,
  );
  if (bins.length === 0) return null;
  return { bins, total: bins.reduce((s, b) => s + b.n, 0) };
}

/* ------------------------------------------------------------------ observations */
export const CERTAINTY_LABEL: Record<Certainty, string> = {
  reported: "Reported fact",
  inference: "Inference",
  correction: "Correction",
};

export function certaintyOf(o: DeskObservation): Certainty {
  return o.certainty === "inference" || o.certainty === "correction" ? o.certainty : "reported";
}

/**
 * Linked corrections. A correction's `corrects` is either an observation id or a flag key "flag:<date>:<game>:<athlete>";
 * the second form is matched to the earlier observation of that game and athlete (ids end "...:<game>:<athlete>").
 */
export function correctionLinks(obs: readonly DeskObservation[]): {
  correctedBy: Map<string, DeskObservation>;
  target: (o: DeskObservation) => DeskObservation | null;
} {
  const byId = new Map(obs.map((o) => [o.id, o]));
  const targets = new Map<string, DeskObservation | null>();
  const find = (c: DeskObservation): DeskObservation | null => {
    if (!c.corrects) return null;
    const direct = byId.get(c.corrects);
    if (direct) return direct;
    const m = /^flag:[^:]*:([^:]+):([^:]+)$/.exec(c.corrects);
    if (!m) return null;
    const tail = `:${m[1] ?? ""}:${m[2] ?? ""}`;
    let best: DeskObservation | null = null;
    for (const o of obs) {
      if (o.id === c.id || o.certainty === "correction" || !o.id.endsWith(tail) || o.ts > c.ts) continue;
      if (best === null || o.ts > best.ts) best = o;
    }
    return best;
  };
  const correctedBy = new Map<string, DeskObservation>();
  for (const o of obs) {
    if (o.certainty !== "correction" && !o.corrects) continue;
    const t = find(o);
    targets.set(o.id, t);
    if (t) {
      const have = correctedBy.get(t.id);
      if (!have || o.ts > have.ts) correctedBy.set(t.id, o);
    }
  }
  return { correctedBy, target: (o) => targets.get(o.id) ?? null };
}

export function newestFirst<T extends { ts: string }>(xs: readonly T[]): T[] {
  return [...xs].sort((a, b) => b.ts.localeCompare(a.ts));
}
