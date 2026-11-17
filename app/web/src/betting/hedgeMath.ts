// Hedge arithmetic for the betting tools: bounded outcome enumeration, hedge sizing against a position's uncovered gross
// payout, and the donor's fixed six-outcome scenario (kept with its tests). Not the app's Position lab (src/hedge.ts).
// Arithmetic on entered prices only: no probability, no suggested size beyond "what covers this payout".
import type { QuotePrice, Side, SlipLeg } from "./types";

export const round6 = (x: number): number => Math.round((x + Number.EPSILON) * 1e6) / 1e6;

// ------------------------------------------------------------------------------------------------ enumeration

/** At most this many distinct markets are enumerated (2^10 = 1024 outcome rows). Above it: "too_many_markets". */
export const MAX_ENUMERATED_MARKETS = 10;

export type OutcomeMap = Readonly<Record<string, Side>>;

export type Enumeration =
  | { readonly ok: true; readonly outcomes: readonly OutcomeMap[] }
  | { readonly ok: false; readonly code: "too_many_markets"; readonly text: string; readonly count: number; readonly cap: number };

/** Every YES/NO combination of these markets, or a typed refusal above the cap. No combination is weighted or dropped. */
export function enumerateOutcomes(marketIds: readonly string[], cap = MAX_ENUMERATED_MARKETS): Enumeration {
  const ids = [...new Set(marketIds)];
  if (ids.length > cap) {
    return { ok: false, code: "too_many_markets", text: `Not shown, because ${ids.length} markets would make ${2 ** Math.min(ids.length, 52)} outcome rows; the limit is ${cap} markets.`, count: ids.length, cap };
  }
  const outcomes: OutcomeMap[] = [];
  for (let i = 0; i < 2 ** ids.length; i++) {
    const row: Record<string, Side> = {};
    ids.forEach((id, j) => {
      row[id] = (i >> j) & 1 ? "yes" : "no";
    });
    outcomes.push(row);
  }
  return { ok: true, outcomes };
}

// ------------------------------------------------------------------------------------------------ native units

/**
 * Gross payout (stake included) for one native unit: one contract on an exchange pays $1; one dollar of sportsbook stake
 * pays 1 + odds/100 (positive odds) or 1 + 100/|odds| (negative). An entry pick has no unit of its own: null.
 */
export function grossPerUnit(price: QuotePrice): number | null {
  if (price.kind === "binary") return 1;
  if (price.kind === "sportsbook") {
    const o = price.americanOdds;
    if (!Number.isFinite(o) || Math.abs(o) < 100) return null;
    return 1 + (o > 0 ? o / 100 : 100 / -o);
  }
  return null;
}

/** A leg's gross payout if it wins, or null when it has none of its own (entry pick) or its amount is not valid. */
export function grossPayoutOf(leg: Pick<SlipLeg, "quote" | "amount">): number | null {
  const unit = grossPerUnit(leg.quote.price);
  if (unit === null || leg.amount === null || !validNativeAmount(leg.quote.price, leg.amount)) return null;
  return round6(leg.amount * unit);
}

/** Whole contracts (1 to 1,000,000) on an exchange; dollars to the cent (0.01 to 1,000,000) at a sportsbook. */
export function validNativeAmount(price: QuotePrice, amount: number): boolean {
  if (!Number.isFinite(amount) || amount < 0.01 || amount > 1_000_000) return false;
  if (price.kind === "binary") return Number.isInteger(amount);
  if (price.kind === "sportsbook") return Math.abs(Math.round(amount * 100) - amount * 100) < 1e-6;
  return false;
}

// ------------------------------------------------------------------------------------------------ hedge sizing

export type UncoveredPayout =
  | { readonly ok: true; readonly dollars: number; readonly legIds: readonly string[] }
  | { readonly ok: false; readonly code: "entry_in_market" | "invalid_position"; readonly text: string };

/**
 * The gross payout of the recorded positions in one market that a new `hedgeSide` position would have to match: payouts
 * on the opposite side add, payouts already on `hedgeSide` subtract, floored at zero. Only existing (recorded) legs count.
 */
export function uncoveredPayout(legs: readonly SlipLeg[], marketId: string, hedgeSide: Side): UncoveredPayout {
  const held = legs.filter((l) => l.role === "existing" && l.quote.marketId === marketId);
  let total = 0;
  for (const l of held) {
    if (l.quote.price.kind === "entry-pick") {
      return { ok: false, code: "entry_in_market", text: "Not sized, because an entry pick in this market pays only with its whole entry." };
    }
    const p = grossPayoutOf(l);
    if (p === null) return { ok: false, code: "invalid_position", text: "Not sized, because a recorded position in this market has an invalid amount or price." };
    total += l.quote.side === hedgeSide ? -p : p;
  }
  return { ok: true, dollars: round6(Math.max(0, total)), legIds: held.map((l) => l.id) };
}

export type HedgeSize =
  | { readonly ok: true; readonly amount: number; readonly unit: "contracts" | "stake_dollars"; readonly grossPayout: number }
  | { readonly ok: false; readonly code: "entry_pick" | "invalid_price" | "nothing_to_cover" | "entry_in_market" | "invalid_position"; readonly text: string };

/**
 * The smallest native amount whose gross payout reaches `targetDollars`: whole contracts rounded UP on an exchange, stake
 * rounded UP to the cent at a sportsbook. An entry pick cannot be sized alone.
 */
export function sizeToPayout(targetDollars: number, price: QuotePrice): HedgeSize {
  if (price.kind === "entry-pick") return { ok: false, code: "entry_pick", text: "Not sized, because an entry pick has no standalone payout." };
  if (!Number.isFinite(targetDollars) || targetDollars <= 0) return { ok: false, code: "nothing_to_cover", text: "Nothing to cover: no uncovered payout in this market." };
  const unit = grossPerUnit(price);
  if (unit === null) return { ok: false, code: "invalid_price", text: "Not sized, because the price is not valid." };
  if (price.kind === "binary") {
    const amount = Math.ceil(round6(targetDollars));
    return { ok: true, amount, unit: "contracts", grossPayout: amount };
  }
  const amount = Math.ceil(round6((targetDollars / unit) * 100)) / 100;
  return { ok: true, amount, unit: "stake_dollars", grossPayout: round6(amount * unit) };
}

/** Sizes a hedge on `hedgeSide` of `marketId` against the recorded positions there (the Hedge Finder flow). */
export function sizeHedge(legs: readonly SlipLeg[], marketId: string, hedgeSide: Side, price: QuotePrice): HedgeSize {
  const u = uncoveredPayout(legs, marketId, hedgeSide);
  if (!u.ok) return u;
  return sizeToPayout(u.dollars, price);
}

// ------------------------------------------------------------------------------------------------ fixed scenario (donor)

/** The donor's fixed paper position: 2 contracts at 39c (78c) on "Collin 3+ threes". Invented, editable numbers. */
export type FixedHedgeId = "no3" | "no2" | "phx";
export interface FixedHedgeLeg {
  readonly id: FixedHedgeId;
  readonly qty: number;
  readonly price: number;
  readonly fee: number;
}
export interface FixedHedgeCandidate {
  readonly id: FixedHedgeId;
  readonly name: string;
  readonly kind: string;
  readonly subtitle: string;
  readonly price: number;
  readonly fee: number;
}
export interface FixedHedgeScenario {
  readonly id: string;
  readonly label: string;
  readonly threes: number;
  readonly phoenixWins: boolean;
  readonly payout: number;
  readonly net: number;
  readonly winners: number;
}
export interface FixedHedgeResult {
  readonly scenarios: readonly FixedHedgeScenario[];
  readonly totalCost: number;
  readonly additionalCost: number;
  readonly worstCase: number;
  readonly bestCase: number;
}

/** Sample candidates (labelled sample on any screen that shows them). */
export const FIXED_SCENARIO_CANDIDATES: readonly FixedHedgeCandidate[] = /* @__PURE__ */ Object.freeze([
  /* @__PURE__ */ Object.freeze({ id: "no3" as const, name: "NO · Collin 3+ threes", kind: "Exact complement", subtitle: "Pays when Collin makes 0–2 threes", price: 52, fee: 1 }),
  /* @__PURE__ */ Object.freeze({ id: "no2" as const, name: "NO · Collin 2+ threes", kind: "Partial coverage", subtitle: "Exactly 2 threes leaves a gap", price: 41, fee: 1 }),
  /* @__PURE__ */ Object.freeze({ id: "phx" as const, name: "YES · Phoenix wins", kind: "Same-game exposure", subtitle: "Both positions can lose", price: 60, fee: 2 }),
]);

// Built on first use: a module-level call could survive tree-shaking in a build that never uses it.
let fixedIds: Set<string> | null = null;
const isFixedId = (id: string): boolean => (fixedIds ??= new Set<string>(FIXED_SCENARIO_CANDIDATES.map((c) => c.id))).has(id);
const FIXED_BASE_COST_C = 78;
const FIXED_BASE_PAYOUT_C = 200;
const asDollars = (cents: number): number => Number((cents / 100).toFixed(8));
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The six exhaustive outcomes of the fixed paper position plus these legs. Throws on any invalid input. */
export function calculateFixedScenarioHedge(legs: unknown): FixedHedgeResult {
  if (!Array.isArray(legs)) throw new TypeError("Hedge legs must be an array.");
  const checked: FixedHedgeLeg[] = [];
  for (const raw of legs as unknown[]) {
    if (raw === null || typeof raw !== "object") throw new TypeError("Unknown hedge settlement condition.");
    const leg = raw as Record<string, unknown>;
    const id = leg["id"];
    if (typeof id !== "string" || !isFixedId(id)) throw new TypeError("Unknown hedge settlement condition.");
    const { qty, price, fee } = leg;
    if (!isNum(qty) || !Number.isInteger(qty) || qty < 1 || qty > 100) throw new RangeError("Quantity must be a whole number from 1 to 100.");
    if (!isNum(price) || price < 0 || price > 100) throw new RangeError("Ask must be from 0 to 100 cents.");
    if (!isNum(fee) || fee < 0 || fee > 100) throw new RangeError("Fee must be from 0 to 100 cents.");
    checked.push({ id: id as FixedHedgeId, qty, price, fee });
  }
  const additionalCostCents = checked.reduce((s, l) => s + l.qty * (l.price + l.fee), 0);
  const totalCostCents = FIXED_BASE_COST_C + additionalCostCents;
  const scenarios: FixedHedgeScenario[] = [];
  for (const threes of [1, 2, 3]) {
    for (const phoenixWins of [true, false]) {
      const originalWins = threes >= 3;
      let payoutCents = originalWins ? FIXED_BASE_PAYOUT_C : 0;
      let winners = originalWins ? 1 : 0;
      for (const l of checked) {
        const wins = l.id === "no3" ? threes < 3 : l.id === "no2" ? threes < 2 : phoenixWins;
        if (wins) {
          payoutCents += l.qty * 100;
          winners += 1;
        }
      }
      scenarios.push({
        id: `${threes}-${phoenixWins ? "win" : "loss"}`,
        label: `${threes === 1 ? "0–1 threes" : threes === 2 ? "Exactly 2 threes" : "3+ threes"} · Phoenix ${phoenixWins ? "wins" : "loses"}`,
        threes,
        phoenixWins,
        payout: asDollars(payoutCents),
        net: asDollars(payoutCents - totalCostCents),
        winners,
      });
    }
  }
  return {
    scenarios,
    totalCost: asDollars(totalCostCents),
    additionalCost: asDollars(additionalCostCents),
    worstCase: Math.min(...scenarios.map((s) => s.net)),
    bestCase: Math.max(...scenarios.map((s) => s.net)),
  };
}
