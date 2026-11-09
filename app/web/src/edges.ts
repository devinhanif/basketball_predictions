// The edge arithmetic for one contract, only from stored fields (GET /api/edges/...): the engine's model chance with its
// range, the ask, the fee and the engine's own edge after fees. Nothing here is computed from other numbers.
import type { Side } from "./legTypes";

export interface EdgeContract {
  side: Side;
  /** "n+" contract: n or more (so an over line of n - 0.5). */
  threshold: number;
  title: string;
  ticker: string;
  price: number;
  fees: number;
  ev: number;
  ev_low: number | null;
  model_prob: number;
  prob_lo: number | null;
  prob_hi: number | null;
  order_type: string;
  price_ts: string;
}

export interface LegEdges {
  report_date: string | null;
  contracts: EdgeContract[];
  note: string;
}

function cents(x: number): string {
  const c = Math.round(x * 1000) / 10;
  return `${Number.isInteger(c) ? c.toFixed(0) : c.toFixed(1)}c`;
}

function signedCents(x: number): string {
  const c = Math.round(x * 1000) / 10;
  const body = Number.isInteger(c) ? Math.abs(c).toFixed(0) : Math.abs(c).toFixed(1);
  return `${c < 0 ? "-" : "+"}${body}c`;
}

function pctText(p: number): string {
  return `${(p * 100).toFixed(1).replace(/\.0$/, "")}%`;
}

export function hasRange(c: EdgeContract): boolean {
  return c.prob_lo !== null && c.prob_hi !== null && c.prob_lo < c.prob_hi;
}

/** "Model 75.5% [75.5% to 75.5%] vs ask 81c + fee 2c = edge -7.5c per $1" */
export function edgeLine(c: EdgeContract): string {
  // A range is shown only when a stored interval has width; a zero-width one would claim certainty the model does not have.
  const range = hasRange(c) ? ` [${pctText(c.prob_lo ?? 0)} to ${pctText(c.prob_hi ?? 0)}]` : " [range not stored]";
  return `Model ${pctText(c.model_prob)}${range} vs ask ${cents(c.price)} + fee ${cents(c.fees)} = edge ${signedCents(c.ev)} per $1`;
}

/** The contract's own words: "6+ assists, Yes" is an over of 5.5; "No" is the under. */
export function contractSide(c: EdgeContract): string {
  return c.side === "over" ? "Yes (over)" : "No (under)";
}

/** Contracts nearest a line first: a contract "n+" sits at the line n - 0.5. */
export function nearestFirst(contracts: readonly EdgeContract[], line: number): EdgeContract[] {
  return [...contracts].sort((a, b) => Math.abs(a.threshold - 0.5 - line) - Math.abs(b.threshold - 0.5 - line) || a.threshold - b.threshold);
}
