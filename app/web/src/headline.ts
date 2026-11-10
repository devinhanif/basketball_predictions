// The biggest text on Today: the night's answer, read from stored counts. Never a slogan, never an invented number.
import type { ParlayState } from "./types";
import { hasTickets, type TpResult } from "./topParlays";

export interface HeadlineInput {
  games: number;
  /** The engine's stored check, and whether it is the check for this very night. */
  engine: ParlayState | null;
  speaks: boolean;
  tickets: TpResult | null;
  /** Counts from the live list only (state words, never scores). */
  live?: LiveCounts;
}

export interface LiveCounts {
  live: number;
  final: number;
}

export interface Headline {
  /** One line with the night's answer. */
  text: string;
  /** "keep" while the verdict is keep your money (the default until the live log proves skill). */
  verdict: "keep" | "flagged";
  /** One plain sentence under it: where the numbers come from. */
  source: string;
}

function n(counts: Record<string, number>, key: string): number | null {
  const v = counts[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function gamesPhrase(games: number): string {
  return `${games.toLocaleString("en-US")} ${games === 1 ? "game" : "games"}`;
}

/** "2 live · 1 final", or "" when none of tonight's games has started. Counts only; never a score. */
export function stateSuffix(c?: LiveCounts): string {
  if (!c) return "";
  return [c.live > 0 ? `${c.live.toString()} live` : "", c.final > 0 ? `${c.final.toString()} final` : ""]
    .filter(Boolean)
    .map((x) => ` · ${x}`)
    .join("");
}

/** The Learn and Explore lens: no contract counts, no edge, no price. Still says keep your money. */
export function learnHeadline(games: number, live?: LiveCounts): Headline {
  return {
    text: `${gamesPhrase(games)} tonight${stateSuffix(live)} · forecasts only · keep your money`,
    verdict: "keep",
    source: "Our model has no live track record yet, so nothing here is a pick. Switch to the Market lens for the market side.",
  };
}

export function headline(i: HeadlineInput): Headline {
  const g = gamesPhrase(i.games) + stateSuffix(i.live);
  const p = i.engine;
  if (p === null) {
    return { text: `${g} · pricing check unavailable · keep your money`, verdict: "keep", source: "Source: the parlay engine. Its check could not be read, which is not a verdict." };
  }
  if (!i.speaks) {
    const t = i.tickets;
    const has = t !== null && t.kind === "ok" && hasTickets(t.data);
    return {
      text: has ? `${g} · unproven tickets listed (paper only) · keep your money` : `${g} · no pricing check was run for this night · keep your money`,
      verdict: "keep",
      source: "Source: the parlay engine. The latest check is for a different night, so it is not shown as this night's.",
    };
  }
  const priced = n(p.counts, "n_contracts");
  const flagged = n(p.counts, "n_flagged_positive");
  if (p.state === "A" && priced !== null && flagged !== null) {
    const record = p.gate !== null ? ` · the model's record so far is n = ${p.gate.n_settled.toLocaleString("en-US")}` : "";
    const keep = p.gate?.gate_open === true ? "" : " · keep your money";
    return {
      text: `${g} · ${flagged.toLocaleString("en-US")} of ${priced.toLocaleString("en-US")} priced contracts flagged by the engine (paper only, unproven)${record}${keep}`,
      verdict: "flagged",
      source: "Source: the parlay engine. It weighs the model and judges each contract at the low end of its range.",
    };
  }
  if (priced !== null && p.headline_verdict === "no_positive_ev_found") {
    return {
      text: `${g} · 0 of ${priced.toLocaleString("en-US")} priced contracts clear an edge after fees · keep your money`,
      verdict: "keep",
      source: "Source: the parlay engine. It weighs the model and judges each contract at the low end of its range, at the ask with the taker fee.",
    };
  }
  return { text: `${g} · ${p.title.toLowerCase()} · keep your money`, verdict: "keep", source: "Source: the parlay engine. It weighs the model and judges each contract at the low end of its range." };
}
