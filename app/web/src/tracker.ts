// Parlay tracker logic. Pure functions: leg status from a live box score, the parlay's value now, a hedge
// calculator, a combo-vs-legs check, and per-viewer persistence. No network, no account, no orders.
import type { BoxPlayer, FcStat, KalshiMarket, LiveGame } from "./liveTypes";

export type LegKind = "player" | "win" | "spread" | "total";
export type LegSide = "over" | "under";

export interface Leg {
  id: string;
  kind: LegKind;
  /** ESPN event id of the game the leg is about. */
  eventId: string;
  /** Short description of the game ("ATL @ ORL") kept so the leg still reads when the scoreboard moves on. */
  gameLabel: string;
  /** Scheduled start (ISO UTC), for ordering exposure in time. */
  startUtc: string;
  player: string;
  stat: FcStat;
  side: LegSide;
  /** player: "N or more" (over) / "fewer than N" (under). total: over/under this score. spread: the team's handicap. */
  line: number;
  /** Team abbreviation: win and spread legs. */
  team: string;
  ticker: string;
  /** Price paid for this leg, in dollars per contract, if bought on its own. */
  entry: number | null;
  /** The viewer's own probability that the leg hits (0..1), overriding the model and the market in the exit signal. */
  pUser?: number | null;
}

export interface Parlay {
  id: string;
  name: string;
  legs: Leg[];
  /** Contracts (each pays $1 if every leg hits). */
  shares: number | null;
  /** Total dollars paid for the whole parlay. */
  totalCost: number | null;
  /** Price per contract of the combo as bought or quoted, in dollars. */
  comboPrice: number | null;
  /** What the viewer could sell the whole parlay for now, per contract, if he typed one in. */
  exitOffer?: number | null;
}

export const STAT_NAME: Record<FcStat, string> = { pts: "points", reb: "rebounds", ast: "assists", fg3m: "3-pointers" };

/* ------------------------------------------------------------------ names */
export function normName(name: string): string {
  const s = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/-/g, " ")
    .replace(/[^a-z ]/g, "");
  return s
    .split(/\s+/)
    .filter((p) => p !== "" && !["jr", "sr", "ii", "iii", "iv"].includes(p))
    .join(" ");
}

export function findPlayer(game: LiveGame, name: string): BoxPlayer | null {
  const n = normName(name);
  if (n === "") return null;
  return game.players.find((p) => normName(p.name) === n) ?? null;
}

/* ------------------------------------------------------------------ leg status */
export type LegState = "pre" | "live" | "done" | "dead" | "unknown";

export interface LegStatus {
  state: LegState;
  /** One line: the count against the need, or the score. */
  progress: string;
  /** Why we can't say, or what rule decides it. */
  note: string | null;
}

export function describeLeg(l: Leg): string {
  switch (l.kind) {
    case "player":
      return l.side === "over"
        ? `${l.player}: ${l.line.toString()}+ ${STAT_NAME[l.stat]}`
        : `${l.player}: fewer than ${l.line.toString()} ${STAT_NAME[l.stat]}`;
    case "win":
      return `${l.team} to win`;
    case "spread":
      return `${l.team} ${l.line > 0 ? "+" : ""}${l.line.toString()}`;
    case "total":
      return `${l.side === "over" ? "Over" : "Under"} ${l.line.toString()} total points`;
  }
}

function clockText(g: LiveGame): string {
  if (g.status.state === "pre") return "not started";
  if (g.status.state === "post") return "final";
  return g.status.detail;
}

const NO_GAME: LegStatus = {
  state: "unknown",
  progress: "game not found in the feed",
  note: "ESPN does not list this game right now, so its status is unknown.",
};

export function legStatus(l: Leg, g: LiveGame | null): LegStatus {
  if (g === null) return NO_GAME;
  const final = g.status.state === "post";
  const started = g.status.state !== "pre";
  const open: LegState = started ? "live" : "pre";
  const home = g.home;
  const away = g.away;
  switch (l.kind) {
    case "player": {
      const p = findPlayer(g, l.player);
      return playerStatus(l, p, final, open, g);
    }
    case "win":
    case "spread": {
      if (!home || !away || home.score === null || away.score === null) return NO_GAME;
      const mine = l.team === home.abbr ? home : l.team === away.abbr ? away : null;
      const other = mine === home ? away : home;
      if (!mine || mine.score === null || other.score === null) {
        return { state: "unknown", progress: "team not in this game", note: `${l.team} is not playing in this game.` };
      }
      const margin = mine.score - other.score;
      const need = l.kind === "win" ? 0 : -l.line;
      const covering = margin > need;
      const progress = `${l.team} ${margin >= 0 ? "+" : ""}${margin.toString()} (${clockText(g)})`;
      if (!final) return { state: open, progress, note: null };
      if (margin === need) return { state: "unknown", progress, note: "Exactly on the line: a push. The market's rules decide." };
      return { state: covering ? "done" : "dead", progress, note: null };
    }
    case "total": {
      if (!home || !away || home.score === null || away.score === null) return NO_GAME;
      const sum = home.score + away.score;
      const progress = `${sum.toString()} of ${l.line.toString()} (${clockText(g)})`;
      if (l.side === "over" && sum > l.line) return { state: "done", progress, note: null };
      if (l.side === "under" && sum > l.line) return { state: "dead", progress, note: null };
      if (!final) return { state: open, progress, note: null };
      if (sum === l.line) return { state: "unknown", progress, note: "Exactly on the line: a push." };
      return { state: l.side === "over" ? "dead" : "done", progress, note: null };
    }
  }
}

function playerStatus(l: Leg, p: BoxPlayer | null, final: boolean, open: LegState, g: LiveGame): LegStatus {
  if (!p) {
    if (final) {
      return {
        state: "unknown",
        progress: "not in the box score",
        note: "Kalshi settles a player who never took the court at a fair price, not yes or no.",
      };
    }
    return { state: open, progress: `not in the box score yet (${clockText(g)})`, note: null };
  }
  const v = p[l.stat];
  if (v === null || p.dnp) {
    return final
      ? { state: "unknown", progress: "did not play", note: "A player who never took the court settles at a fair price." }
      : { state: open, progress: `no line yet (${clockText(g)})`, note: null };
  }
  const progress = `${v.toString()} ${STAT_NAME[l.stat]}, need ${l.side === "over" ? "" : "under "}${l.line.toString()} (${clockText(g)})`;
  if (l.side === "over") {
    if (v >= l.line) return { state: "done", progress, note: null };
    return { state: final ? "dead" : open, progress, note: null };
  }
  if (v >= l.line) return { state: "dead", progress, note: null };
  return { state: final ? "done" : open, progress, note: null };
}

/** A settled Kalshi market overrides the feed: the market's own result is what pays. */
export function withMarket(s: LegStatus, m: KalshiMarket | undefined): LegStatus {
  if (m?.result === "yes") return { state: "done", progress: s.progress, note: "Settled yes on Kalshi." };
  if (m?.result === "no") return { state: "dead", progress: s.progress, note: "Settled no on Kalshi." };
  return s;
}

/* ------------------------------------------------------------------ prices */
export interface LegPrice {
  yesBid: number | null;
  yesAsk: number | null;
  noBid: number | null;
  noAsk: number | null;
  last: number | null;
}

export function priceOf(m: KalshiMarket | undefined): LegPrice | null {
  if (!m?.found) return null;
  return { yesBid: m.yes_bid, yesAsk: m.yes_ask, noBid: m.no_bid, noAsk: m.no_ask, last: m.last };
}

/** Mid of bid and ask when both exist; otherwise the last trade; otherwise unknown. */
export function mid(p: LegPrice | null): number | null {
  if (!p) return null;
  if (p.yesBid !== null && p.yesAsk !== null) return (p.yesBid + p.yesAsk) / 2;
  return p.last;
}

/** What a leg is worth now per $1: 1 if it has hit, 0 if dead, else the market's mid (null without a price). */
export function legValue(s: LegStatus, p: LegPrice | null): number | null {
  if (s.state === "done") return 1;
  if (s.state === "dead") return 0;
  return mid(p);
}

/* ------------------------------------------------------------------ fees */
/**
 * Kalshi's published taker fee is about 0.07 x contracts x p x (1 - p), rounded up to the cent. We use that
 * approximation (the repo's fee config lives outside app/ and is not read here). Labeled wherever it is shown.
 */
export const FEE_RATE = 0.07;

export function takerFee(contracts: number, price: number): number {
  if (!(contracts > 0) || price <= 0 || price >= 1) return 0;
  return Math.ceil(FEE_RATE * contracts * price * (1 - price) * 100 - 1e-9) / 100;
}

/* ------------------------------------------------------------------ parlay value */
export interface LegRow {
  leg: Leg;
  status: LegStatus;
  price: LegPrice | null;
  value: number | null;
}

export interface ParlaySummary {
  rows: LegRow[];
  /** All legs hit or any leg dead, or still open. */
  state: "won" | "dead" | "open";
  /** Fair value now of the whole position = shares x product of leg values (independence assumed). */
  fairValue: number | null;
  /** Product of leg values (probability the whole parlay hits if legs were independent). */
  jointP: number | null;
  paid: number | null;
  /** fairValue - paid. */
  gainNow: number | null;
  /** The open leg with the lowest price: carries most of the remaining risk. */
  riskLeg: LegRow | null;
  /** Open legs with no price, which stop the product from being computed. */
  unpriced: LegRow[];
}

export function paidOf(p: Parlay): number | null {
  if (p.totalCost !== null) return p.totalCost;
  if (p.comboPrice !== null && p.shares !== null) return p.comboPrice * p.shares;
  return null;
}

export function summarize(p: Parlay, status: (l: Leg) => LegStatus, price: (l: Leg) => LegPrice | null): ParlaySummary {
  const rows: LegRow[] = p.legs.map((leg) => {
    const s = status(leg);
    const pr = price(leg);
    return { leg, status: s, price: pr, value: legValue(s, pr) };
  });
  const dead = rows.some((r) => r.status.state === "dead");
  const allDone = rows.length > 0 && rows.every((r) => r.status.state === "done");
  const open = rows.filter((r) => r.status.state !== "done" && r.status.state !== "dead");
  const unpriced = open.filter((r) => r.value === null);
  let jointP: number | null = null;
  if (dead) jointP = 0;
  else if (rows.length > 0 && unpriced.length === 0) jointP = rows.reduce((a, r) => a * (r.value ?? 0), 1);
  const paid = paidOf(p);
  const fairValue = jointP === null || p.shares === null ? null : jointP * p.shares;
  const priced = open.filter((r) => r.value !== null);
  const riskLeg = priced.length === 0 ? null : priced.reduce((a, b) => ((b.value ?? 1) < (a.value ?? 1) ? b : a));
  return {
    rows,
    state: dead ? "dead" : allDone ? "won" : "open",
    fairValue,
    jointP,
    paid,
    gainNow: fairValue === null || paid === null ? null : fairValue - paid,
    riskLeg,
    unpriced,
  };
}

/* ------------------------------------------------------------------ exit signal */
export interface LegModel {
  p: number | null;
  source: string | null;
  reason: string | null;
}

export interface ExitLeg {
  leg: Leg;
  /** Probability at entry (pre-game), and where it came from. */
  entryP: number | null;
  entrySource: string;
  /** Probability now, and where it came from. */
  nowP: number | null;
  nowSource: string;
}

export interface ExitSignal {
  legs: ExitLeg[];
  entryModelValue: number | null;
  /** entryModelValue - paid: what the model thought the position was worth beyond its price. */
  entryEdge: number | null;
  modelNow: number | null;
  /** What exiting would bring now: the viewer's typed offer, or the legs sold at their bids. */
  offer: number | null;
  offerSource: string;
  /** Value at the mids less the offer: what the spread costs to leave. */
  spreadCost: number | null;
  verdict: "gone" | "remains" | "settled" | "unknown";
  /** True when no open leg carries a number from the viewer: then "now" is the market mid and the offer sits below it by the spread alone. */
  marketOnly: boolean;
  missing: string[];
}

const product = (xs: (number | null)[]): number | null => (xs.length > 0 && xs.every((x) => x !== null) ? xs.reduce((a: number, b) => a * b, 1) : null);

export function exitSignal(p: Parlay, rows: LegRow[], models: Record<string, LegModel | undefined>): ExitSignal {
  const legs: ExitLeg[] = rows.map((r) => {
    const m = models[r.leg.id];
    const user = r.leg.pUser ?? null;
    const entryP = user ?? m?.p ?? null;
    const entrySource = user !== null ? "your number" : m?.p != null ? (m.source ?? "model") : (m?.reason ?? "no model probability");
    let nowP: number | null;
    let nowSource: string;
    if (r.status.state === "done") [nowP, nowSource] = [1, "settled: hit"];
    else if (r.status.state === "dead") [nowP, nowSource] = [0, "settled: missed"];
    else if (user !== null) [nowP, nowSource] = [user, "your number"];
    else if (r.value !== null) [nowP, nowSource] = [r.value, "Kalshi mid (there is no in-game model)"];
    else [nowP, nowSource] = [null, "no price and no number from you"];
    return { leg: r.leg, entryP, entrySource, nowP, nowSource };
  });
  const shares = p.shares;
  const paid = paidOf(p);
  const j0 = product(legs.map((l) => l.entryP));
  const entryModelValue = j0 === null || shares === null ? null : j0 * shares;
  const j1 = product(legs.map((l) => l.nowP));
  const modelNow = j1 === null || shares === null ? null : j1 * shares;
  const bids = rows.map((r) => (r.status.state === "done" ? 1 : r.status.state === "dead" ? 0 : r.price?.yesBid ?? null));
  const bidProd = product(bids);
  const typed = p.exitOffer ?? null;
  const offer = typed !== null && shares !== null ? typed * shares : bidProd === null || shares === null ? null : bidProd * shares;
  const midProd = product(rows.map((r) => r.value));
  const spreadCost = offer === null || midProd === null || shares === null ? null : midProd * shares - offer;
  const missing: string[] = [];
  if (shares === null) missing.push("number of contracts");
  legs.forEach((l) => {
    if (l.entryP === null) missing.push(`entry probability for ${describeLeg(l.leg)}`);
  });
  const settled = rows.some((r) => r.status.state === "dead") || (rows.length > 0 && rows.every((r) => r.status.state === "done"));
  const verdict = settled ? "settled" : modelNow === null || offer === null || entryModelValue === null ? "unknown" : offer >= modelNow ? "gone" : "remains";
  const marketOnly = !rows.some((r, i) => r.status.state !== "done" && r.status.state !== "dead" && (legs[i]?.leg.pUser ?? null) !== null);
  return {
    legs,
    entryModelValue,
    entryEdge: entryModelValue === null || paid === null ? null : entryModelValue - paid,
    modelNow,
    offer,
    offerSource: typed !== null ? "the offer you typed" : "each leg sold at its Kalshi bid",
    spreadCost,
    verdict,
    marketOnly,
    missing,
  };
}

/* ------------------------------------------------------------------ hedge */
export interface HedgeInput {
  shares: number;
  paid: number;
  /** What a "No" contract on the chosen leg costs to buy now (the No ask). */
  noAsk: number;
  /** Mid of the No side, to measure the spread paid. */
  noMid: number;
  /** Number of "No" contracts bought. */
  count: number;
}

export interface HedgeResult {
  cost: number;
  fee: number;
  /** Spread drag: what buying at the ask costs over buying at the mid. */
  spread: number;
  /** Net result in each world, after what was paid for the parlay, the hedge and its fee. */
  netAllHit: number;
  netLegMisses: number;
  /** The chosen leg hits but another open leg misses, so the parlay pays nothing and the hedge pays nothing. */
  netOtherMisses: number;
}

/** The number of No contracts that makes "leg hits and everything else hits" pay the same as "leg misses". */
export function fullHedgeCount(shares: number): number {
  return shares;
}

export function hedge(i: HedgeInput): HedgeResult {
  const cost = i.count * i.noAsk;
  const fee = takerFee(i.count, i.noAsk);
  return {
    cost,
    fee,
    spread: i.count * (i.noAsk - i.noMid),
    netAllHit: i.shares - i.paid - cost - fee,
    netLegMisses: i.count - i.paid - cost - fee,
    netOtherMisses: -i.paid - cost - fee,
  };
}

/* ------------------------------------------------------------------ combo vs legs */
export interface ComboCheck {
  combo: number;
  productAsk: number | null;
  productMid: number | null;
  /** productAsk - combo: positive means the combo is cheaper than the legs bought one by one at their asks. */
  gapVsAsk: number | null;
  below: boolean;
}

export function comboCheck(combo: number, rows: LegRow[]): ComboCheck {
  const ask = (r: LegRow): number | null =>
    r.status.state === "done" ? 1 : r.status.state === "dead" ? 0 : r.price?.yesAsk ?? null;
  const asks = rows.map(ask);
  const productAsk = asks.length > 0 && asks.every((a) => a !== null) ? asks.reduce((a: number, b) => a * b, 1) : null;
  const mids = rows.map((r) => r.value);
  const productMid = mids.length > 0 && mids.every((m) => m !== null) ? mids.reduce((a: number, b) => a * b, 1) : null;
  const gapVsAsk = productAsk === null ? null : productAsk - combo;
  return { combo, productAsk, productMid, gapVsAsk, below: gapVsAsk !== null && gapVsAsk > 0 };
}

/* ------------------------------------------------------------------ persistence (per viewer, convenience only) */
export const STORE_KEY = "ph.tracker.v1";

export interface StoreLike {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}

const KINDS: readonly string[] = ["player", "win", "spread", "total"];

function isLeg(x: unknown): x is Leg {
  if (typeof x !== "object" || x === null) return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o["id"] === "string" &&
    typeof o["kind"] === "string" &&
    KINDS.includes(o["kind"]) &&
    typeof o["eventId"] === "string" &&
    typeof o["line"] === "number" &&
    typeof o["team"] === "string" &&
    typeof o["player"] === "string" &&
    typeof o["ticker"] === "string" &&
    (o["side"] === "over" || o["side"] === "under") &&
    typeof o["stat"] === "string"
  );
}

function isParlay(x: unknown): x is Parlay {
  if (typeof x !== "object" || x === null) return false;
  const o = x as Record<string, unknown>;
  return typeof o["id"] === "string" && typeof o["name"] === "string" && Array.isArray(o["legs"]) && o["legs"].every(isLeg);
}

export function loadParlays(store: StoreLike | null): Parlay[] {
  try {
    const raw = store?.getItem(STORE_KEY);
    if (!raw) return [];
    const v: unknown = JSON.parse(raw);
    return Array.isArray(v) ? v.filter(isParlay) : [];
  } catch {
    return [];
  }
}

export function saveParlays(store: StoreLike | null, parlays: Parlay[]): boolean {
  try {
    store?.setItem(STORE_KEY, JSON.stringify(parlays));
    return store !== null;
  } catch {
    return false;
  }
}

export function browserStore(): StoreLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function money(x: number): string {
  const s = Math.abs(x).toFixed(2);
  return x < 0 ? `-$${s}` : `$${s}`;
}
