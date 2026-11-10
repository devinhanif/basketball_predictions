// "Today's picks": the morning run's closest contracts, said in plain words. Pure helpers; every number is a stored field of
// the export (parlay_of_day.json through /api/contracts/top). Template text only: no model, no invented number, no stake.
import type { TopContract } from "./api";
import { engineSpeaksFor } from "./nights";
import { pct } from "./format";

export const PICK_COUNT = 5;
const FETCH_COUNT = 50;
export const PICKS_FETCH = FETCH_COUNT;

const STAT_WORD: Record<string, string> = { pts: "points", reb: "rebounds", ast: "assists", fg3m: "threes" };
export function statWord(stat: string): string {
  return STAT_WORD[stat] ?? stat;
}

/** "Grayson Allen 2+ threes" (over) or "Grayson Allen fewer than 2 threes" (under). */
export function betWords(c: Pick<TopContract, "player" | "side" | "threshold" | "stat">): string {
  const t = Number.isInteger(c.threshold) ? c.threshold.toFixed(0) : c.threshold.toFixed(1);
  return c.side === "over" ? `${c.player} ${t}+ ${statWord(c.stat)}` : `${c.player} fewer than ${t} ${statWord(c.stat)}`;
}

/** The best few by edge after fees, one per player and stat (the over and the under of one line are one question). */
export function selectPicks(contracts: readonly TopContract[], n = PICK_COUNT): TopContract[] {
  const seen = new Set<string>();
  const out: TopContract[] = [];
  for (const c of [...contracts].sort((a, b) => b.ev - a.ev || b.model_prob - a.model_prob)) {
    const key = `${c.game_id}|${c.player_id.toString()}|${c.stat}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(c);
    if (out.length === n) break;
  }
  return out;
}

/** Dollars per $1 after fees, signed, to the tenth of a cent: "-$0.015". */
export function evPerDollar(ev: number): string {
  const r = Math.round(ev * 1000) / 1000;
  return `${r < 0 ? "-" : "+"}$${Math.abs(r).toFixed(3)}`;
}

export function priceCents(price: number): string {
  const c = Math.round(price * 1000) / 10;
  return `${c.toFixed(Number.isInteger(c) ? 0 : 1)}c`;
}

/** One or two sentences from stored fields only; a sentence is left out when its field is not stored. */
export function whyText(c: TopContract): string {
  const x = c.context;
  const first: string[] = [];
  if (x) {
    if (x.proj_minutes !== null && x.p_play !== null) {
      first.push(`projected ${x.proj_minutes.toFixed(0)} minutes with a ${pct(x.p_play)} chance he plays`);
    } else if (x.proj_minutes !== null) {
      first.push(`projected ${x.proj_minutes.toFixed(0)} minutes`);
    } else if (x.p_play !== null) {
      first.push(`${pct(x.p_play)} chance he plays`);
    }
    if (x.mean_cond !== null) {
      const t = Number.isInteger(c.threshold) ? c.threshold.toFixed(0) : c.threshold.toFixed(1);
      first.push(`${x.mean_cond.toFixed(1)} ${statWord(c.stat)} expected if he plays, against a line of ${t}`);
    }
    if (x.teammates_out.length > 0) first.push(`${x.teammates_out.join(", ")} listed out`);
  }
  const a = first.length > 0 ? `${first.join("; ")}.`.replace(/^./, (m) => m.toUpperCase()) : "";
  const price = priceCents(c.price);
  const fee = priceCents(c.fees);
  const w = x?.model_weight ?? null;
  const b =
    w === 0
      ? `The model defers to the market until its record clears the gate, so its chance is the market's middle, ${pct(c.model_prob)}; buying at the ${price} ask plus the ${fee} fee leaves it ${c.ev < 0 ? "negative" : "positive"}.`
      : `Model ${pct(c.model_prob)} against a ${price} price and a ${fee} fee.`;
  return a === "" ? b : `${a} ${b}`;
}

export type PicksHead =
  | { kind: "none"; text: string }
  | { kind: "negative"; text: string }
  | { kind: "positive"; text: string };

/** The line at the top: the honest "no bet is positive" is the usual case and says so plainly. */
export function picksHead(verdict: string | null, nEvaluated: number | null, nPositive: number | null, shown: TopContract[]): PicksHead {
  if (shown.length === 0) return { kind: "none", text: "No priced player contract is stored for this morning's run." };
  const ev = nEvaluated === null ? "" : ` ${nEvaluated.toLocaleString("en-US")} contracts evaluated, ${(nPositive ?? 0).toLocaleString("en-US")} positive after fees.`;
  const anyPositive = shown.some((c) => c.ev > 0) && (nPositive ?? 0) > 0;
  if (verdict === "no_positive_ev_found" || !anyPositive) {
    return { kind: "negative", text: `No bet is positive after fees this morning; these are the closest.${ev}` };
  }
  return { kind: "positive", text: `${(nPositive ?? 0).toLocaleString("en-US")} contract${nPositive === 1 ? "" : "s"} cleared the engine's bound after fees.${ev}` };
}

/** True when the morning run is for the night on screen (a run for another night is never shown as this night's). */
export function runIsForNight(date: string, reportDate: string | null): boolean {
  return reportDate !== null && engineSpeaksFor(date, reportDate);
}
