/** Bankroll plan types and small helpers. Every figure comes from the API file; nothing is invented here. */

export type Tier = "SAFE" | "MEDIUM" | "RISKY";
export type MoveStatus = "todo" | "placed" | "skipped";

export interface Move {
  id: string;
  tier: Tier;
  title: string;
  stake: number;
  source: "bonus" | "cash";
  limit: number;
  payout: number;
  chance: number;
  reason: string;
  status: MoveStatus;
}

export interface Position {
  id: string;
  title: string;
  legs: string[];
  cost: number;
  source: "bonus" | "cash";
  max_payout: number;
  fair_now: number;
  sell_if_offer_at_least: number;
}

export interface ScoreRow {
  date: string;
  end_cash: number;
  pnl: number;
  pnl_by_tier: Record<string, number>;
  bonus_converted: number;
}

export interface Bankroll {
  as_of: string;
  month_start: { date: string; balance: number };
  account: { cash: number; bonus: number };
  derived: {
    months: number;
    floor: number;
    risk_capital: number;
    budget: number;
    cash_planned: number;
    target: number;
    stretch: number;
  };
  moves: Move[];
  positions: Position[];
  scorecard: ScoreRow[];
  budget_override: string | null;
  note: string;
}

export const STATUS_TEXT: Record<MoveStatus, string> = { todo: "To do", placed: "Placed", skipped: "Skipped" };

export function usd(n: number): string {
  const s = Math.abs(n).toFixed(2);
  return n < 0 ? `-$${s}` : `$${s}`;
}

export function signedUsd(n: number): string {
  return n > 0 ? `+${usd(n)}` : usd(n);
}

/** A price in cents, with a decimal only when it has one: 0.28 -> "28¢", 0.077 -> "7.7¢". */
export function cents(p: number): string {
  const c = Math.round(p * 1000) / 10;
  return `${Number.isInteger(c) ? c.toFixed(0) : c.toFixed(1)}¢`;
}

export function chancePct(p: number): string {
  return `${Math.round(p * 100).toString()}%`;
}

/** Where the cash sits on a bar from month start (0) to the stretch (1), and where the target falls. */
export function monthProgress(cash: number, start: number, target: number, stretch: number): { fill: number; targetAt: number; delta: number } {
  const span = stretch - start;
  const fill = span > 0 ? Math.min(Math.max((cash - start) / span, 0), 1) : 0;
  const targetAt = span > 0 ? (target - start) / span : 0.2;
  return { fill, targetAt, delta: Math.round((cash - start) * 100) / 100 };
}
