// Hedge Finder ("Position lab"): what a paper position pays in every combination of a player's stat and the game result.
// Strictly arithmetic on what the viewer enters plus stored prices; each contract pays 100 cents if it wins and nothing if
// it loses. No orders, no advice, no suggested size. Money is in cents.
import type { EdgeContract } from "./edges";

export const PAY_C = 100;
export const MAX_CONTRACTS = 1000;

export interface HedgeLeg {
  id: string;
  kind: "stat" | "team";
  side: "yes" | "no";
  label: string;
  /** stat legs: "k+" wins (YES) when the count is k or more. */
  threshold?: number;
  /** team legs: YES wins when that team wins. */
  team?: "home" | "away";
  contracts: number;
  /** False until a fee is stored or typed: results are then "before fee", never silently counted as a zero fee. */
  fee_set: boolean;
  /** Ask in cents, 1 to 99. 0 means not entered yet (the leg is left out until it is). */
  ask_c: number;
  /** Fee per contract in cents. */
  fee_c: number;
  source: "stored" | "entered";
}

export interface GameCtx {
  home: string;
  away: string;
}

function clampInt(x: number, lo: number, hi: number): number {
  return Number.isFinite(x) ? Math.min(hi, Math.max(lo, Math.round(x))) : lo;
}

/** Keep every entered number in a sane range (integers of contracts; ask 1-99; fee 0-50, to a tenth of a cent). */
export function clampLeg(l: HedgeLeg): HedgeLeg {
  return {
    ...l,
    contracts: clampInt(l.contracts, 0, MAX_CONTRACTS),
    ask_c: clampInt(l.ask_c, 0, 99),
    fee_c: Number.isFinite(l.fee_c) ? Math.min(50, Math.max(0, Math.round(l.fee_c * 10) / 10)) : 0,
  };
}

/** Does this leg win when the stat finished at `count` and the game went to `winner`? */
export function legWins(l: HedgeLeg, count: number, winner: "home" | "away" | null): boolean {
  if (l.kind === "stat") {
    const k = l.threshold ?? 0;
    return l.side === "yes" ? count >= k : count < k;
  }
  const teamWon = winner !== null && winner === l.team;
  return l.side === "yes" ? teamWon : !teamWon;
}

/** What entering the leg costs: contracts x (ask + fee), in cents. */
export function legCost(l: HedgeLeg): number {
  return Math.round(l.contracts * (l.ask_c + l.fee_c) * 10) / 10;
}
export function totalCost(legs: readonly HedgeLeg[]): number {
  return Math.round(legs.reduce((s, l) => s + legCost(l), 0) * 10) / 10;
}

export interface Bucket {
  label: string;
  /** The smallest count in the bucket (every count in it is settled the same way by every leg). */
  from: number;
}

/** Stat buckets cut at every "k+" threshold in play: [0, t1), [t1, t2), ..., [tm, up). */
export function buckets(legs: readonly HedgeLeg[]): Bucket[] {
  const ts = [...new Set(legs.filter((l) => l.kind === "stat" && l.threshold !== undefined).map((l) => l.threshold as number))].sort((a, b) => a - b);
  if (ts.length === 0) return [{ label: "any count", from: 0 }];
  const out: Bucket[] = [];
  const first = ts[0] as number;
  if (first > 0) out.push({ label: first === 1 ? "0" : `0-${(first - 1).toString()}`, from: 0 });
  ts.forEach((t, i) => {
    const nxt = ts[i + 1];
    if (nxt === undefined) out.push({ label: `${t.toString()}+`, from: t });
    else out.push({ label: nxt - t === 1 ? `exactly ${t.toString()}` : `${t.toString()}-${(nxt - 1).toString()}`, from: t });
  });
  return out;
}

export interface Outcome {
  id: string;
  label: string;
  winner: "home" | "away" | null;
  /** Cents paid out, and net of the cost of every leg. */
  payout_c: number;
  net_c: number;
  /** How many positions pay in this outcome. */
  paying: number;
}

/** A leg counts once it has contracts and an ask. */
export function isLive(l: HedgeLeg): boolean {
  return l.contracts > 0 && l.ask_c > 0;
}

export function outcomes(legs: readonly HedgeLeg[], game: GameCtx): Outcome[] {
  const live = legs.filter(isLive);
  const hasTeam = live.some((l) => l.kind === "team");
  const winners: ("home" | "away" | null)[] = hasTeam ? ["home", "away"] : [null];
  const cost = totalCost(live);
  const out: Outcome[] = [];
  for (const b of buckets(live)) {
    for (const w of winners) {
      let payout = 0;
      let paying = 0;
      for (const l of live) {
        if (legWins(l, b.from, w)) {
          payout += l.contracts * PAY_C;
          paying++;
        }
      }
      const who = w === null ? "" : ` · ${w === "home" ? game.home : game.away} wins`;
      out.push({ id: `${b.label}|${w ?? "-"}`, label: `${b.label}${who}`, winner: w, payout_c: payout, net_c: Math.round((payout - cost) * 10) / 10, paying });
    }
  }
  return out;
}

export interface Summary {
  worst_c: number;
  best_c: number;
  /** True when a live leg has no fee yet: every net here is before fee. */
  before_fee: boolean;
  /** True when every outcome nets above zero at the prices entered (still unverified settlement). */
  positive_everywhere: boolean;
  cost_c: number;
}

export function summarize(legs: readonly HedgeLeg[], game: GameCtx): Summary | null {
  const live = legs.filter(isLive);
  if (live.length === 0) return null;
  const os = outcomes(live, game);
  const nets = os.map((o) => o.net_c);
  const worst = Math.min(...nets);
  return { worst_c: worst, best_c: Math.max(...nets), positive_everywhere: worst > 0, cost_c: totalCost(live), before_fee: live.some((l) => !l.fee_set) };
}

export function centsText(c: number): string {
  const r = Math.round(c * 10) / 10;
  return `${r < 0 ? "-" : r > 0 ? "+" : ""}${Math.abs(r).toFixed(Number.isInteger(r) ? 0 : 1)}c`;
}
export function plainCents(c: number): string {
  const r = Math.round(c * 10) / 10;
  return `${r.toFixed(Number.isInteger(r) ? 0 : 1)}c`;
}

/* ------------------------------------------------------------------ building legs from stored contracts */
let seq = 0;
export function newId(): string {
  seq += 1;
  return `leg-${seq.toString()}`;
}

/**
 * A leg in words that carry their own side: YES on "k+" is "k or more", NO on "k+" is "fewer than k". The side is said once,
 * so a NO is never also written "NO fewer than".
 */
export function legWords(side: "yes" | "no", threshold: number, statLabel: string): string {
  return side === "yes" ? `${threshold.toString()} or more ${statLabel}` : `fewer than ${threshold.toString()} ${statLabel}`;
}

/** A leg from one stored contract row (price and fee come from the row: dollars to cents). */
export function legFromContract(c: EdgeContract, statLabel: string, contracts = 1, who?: string): HedgeLeg {
  const side = c.side === "over" ? "yes" : "no";
  return clampLeg({
    id: newId(), kind: "stat", side, label: `${who ? `${who}: ` : ""}${legWords(side, c.threshold, statLabel)}`,
    threshold: c.threshold, contracts, ask_c: c.price * 100, fee_c: c.fees * 100, fee_set: true, source: "stored",
  });
}

/** The pieces of a desk play the position lab needs. */
export interface PlayLike {
  side: string;
  threshold: number | null;
  price_cents: number | null;
  fee_cents: number | null;
  leg_desc: string | null;
  player_name: string | null;
}

/**
 * A leg from a desk play. The play's own wording (leg_desc) already says the side ("fewer than 6 assists"), so it is the label
 * as is. `side` "no" on threshold k pays when the count is below k; "yes" pays at k or more. `fee_c` is the stored fee, else
 * the one the caller found in a stored contract row, else 0 until the viewer enters one. Null when the play has no stat line.
 */
export function legFromPlay(p: PlayLike, statLabel: string, fee_c: number | null, source: "stored" | "entered"): HedgeLeg | null {
  if (p.threshold === null || p.price_cents === null) return null;
  const side = p.side === "no" ? "no" : "yes";
  return clampLeg({
    id: newId(), kind: "stat", side, label: p.leg_desc ?? `${p.player_name ?? "Unnamed player"}: ${legWords(side, p.threshold, statLabel)}`,
    threshold: p.threshold, contracts: 1, ask_c: p.price_cents, fee_c: fee_c ?? 0, fee_set: fee_c !== null, source,
  });
}

export interface Candidate {
  leg: HedgeLeg;
  note: string;
}

/** Does adding `cand` to `base` leave some stat count where neither pays? */
export function leavesGap(base: HedgeLeg, cand: HedgeLeg): boolean {
  if (cand.kind === "team") return true;
  const os = outcomes([{ ...base, contracts: 1, ask_c: Math.max(1, base.ask_c) }, { ...cand, contracts: 1, ask_c: Math.max(1, cand.ask_c) }], { home: "H", away: "A" });
  return os.some((o) => o.paying === 0);
}

/**
 * Candidates from the same player and stat (stored rows, with their stored ask and fee) and the two game-result legs
 * (no stored price: the viewer enters one). Each carries a plain note about coverage.
 */
export function candidatesFor(base: HedgeLeg, stored: readonly EdgeContract[], statLabel: string, game: GameCtx): Candidate[] {
  const out: Candidate[] = [];
  const seen = new Set<string>([`${base.side}|${(base.threshold ?? 0).toString()}`]);
  const sorted = [...stored].sort((a, b) => Math.abs(a.threshold - (base.threshold ?? 0)) - Math.abs(b.threshold - (base.threshold ?? 0)) || a.threshold - b.threshold);
  for (const c of sorted) {
    const side = c.side === "over" ? "yes" : "no";
    const key = `${side}|${c.threshold.toString()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const leg = legFromContract(c, statLabel, base.contracts);
    const exact = c.threshold === base.threshold && side !== base.side;
    const gap = leavesGap(base, leg);
    out.push({ leg, note: exact ? "Exact complement: one of the two always pays" : gap ? "Leaves a gap: some counts pay on neither" : "Together they pay on every count" });
    if (out.length >= 5) break;
  }
  const hasExact = out.some((c) => c.leg.kind === "stat" && c.leg.threshold === base.threshold && c.leg.side !== base.side);
  if (!hasExact && base.kind === "stat") {
    out.unshift({
      leg: clampLeg({ id: newId(), kind: "stat", side: base.side === "yes" ? "no" : "yes", label: legWords(base.side === "yes" ? "no" : "yes", base.threshold ?? 0, statLabel), threshold: base.threshold, contracts: 0, ask_c: 0, fee_c: base.fee_c, fee_set: base.fee_set, source: "entered" }),
      note: "Exact complement: one of the two always pays. No stored price: enter contracts and your own ask",
    });
  }
  for (const team of ["home", "away"] as const) {
    out.push({
      leg: clampLeg({ id: newId(), kind: "team", side: "yes", team, label: `${team === "home" ? game.home : game.away} wins`, contracts: 0, ask_c: 0, fee_c: base.fee_c, fee_set: base.fee_set, source: "entered" }),
      note: "No stored price: enter contracts and your own ask. Both positions can lose",
    });
  }
  return out;
}
