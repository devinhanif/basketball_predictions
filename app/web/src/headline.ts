// The biggest text on Today: the night's answer, read from stored counts. Never a slogan, never an invented number.
import type { ParlayState } from "./types";
import { hasTickets, type TpResult } from "./topParlays";

export interface HeadlineInput {
  games: number;
  /** The engine's stored check, and whether it is the check for this very night. */
  engine: ParlayState | null;
  speaks: boolean;
  tickets: TpResult | null;
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

export function headline(i: HeadlineInput): Headline {
  const g = gamesPhrase(i.games);
  const p = i.engine;
  if (p === null) {
    return { text: `${g} · pricing check unavailable · keep your money`, verdict: "keep", source: "The engine's check could not be read, which is not a verdict." };
  }
  if (!i.speaks) {
    const t = i.tickets;
    const has = t !== null && t.kind === "ok" && hasTickets(t.data);
    return {
      text: has ? `${g} · unproven tickets listed (paper only) · keep your money` : `${g} · no pricing check is stored for this night · keep your money`,
      verdict: "keep",
      source: "The parlay engine's stored check is for a different night, so it is not shown as this night's.",
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
      source: "From the parlay engine's stored check for this night.",
    };
  }
  if (priced !== null && p.headline_verdict === "no_positive_ev_found") {
    return {
      text: `${g} · 0 of ${priced.toLocaleString("en-US")} priced contracts clear an edge after fees · keep your money`,
      verdict: "keep",
      source: "From the parlay engine's stored check for this night: every contract priced at the ask, with the taker fee.",
    };
  }
  return { text: `${g} · ${p.title.toLowerCase()} · keep your money`, verdict: "keep", source: "From the parlay engine's stored check for this night." };
}
