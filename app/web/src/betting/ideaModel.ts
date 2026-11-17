// Saved ideas as plain data for the Ideas screens: search and status filters, each leg's paper result, the idea's paper net
// (the outcome row of its own saved prices, nothing else), and the ideas file (export and a merging import that changes
// nothing when the file is invalid). Every number is from evaluateDraft on the idea's frozen snapshot; no probability, no
// ranking, no correlation score.
import { MAX_IMPORT_CHARS } from "./repository";
import { activeLegs, emptyDraft, evaluateDraft, ideaMarketIds, validateSlipWorkspace, type SlipEvaluation, type SlipRow } from "./slipModel";
import type { IdeaStatus, SavedIdea, SlipLeg, SlipWorkspace } from "./types";

export type IdeaFilter = "all" | IdeaStatus;
export const IDEA_FILTERS: readonly IdeaFilter[] = ["all", "watching", "paper", "settled"];
export const STATUS_WORD: Readonly<Record<IdeaStatus, string>> = { watching: "Watching", paper: "Paper", settled: "Settled" };

/** The idea's searchable text: title, thesis, tags, players, games and venues. */
function haystack(idea: SavedIdea): string {
  const legs = idea.legs.map((l) => `${l.market.playerName} ${l.market.eventLabel} ${l.quote.provider} ${l.market.stat}`).join(" ");
  return `${idea.title} ${idea.thesis} ${idea.tags.join(" ")} ${legs}`.toLowerCase();
}

/** Ideas matching every word of the query and the status, newest saved first (by save time, never by any value). */
export function filterIdeas(ideas: readonly SavedIdea[], query: string, status: IdeaFilter): SavedIdea[] {
  const words = query.toLowerCase().split(/\s+/).filter((w) => w !== "");
  return ideas
    .filter((i) => (status === "all" || i.status === status) && words.every((w) => haystack(i).includes(w)))
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0));
}

export function statusCounts(ideas: readonly SavedIdea[]): Record<IdeaFilter, number> {
  return { all: ideas.length, watching: ideas.filter((i) => i.status === "watching").length, paper: ideas.filter((i) => i.status === "paper").length, settled: ideas.filter((i) => i.status === "settled").length };
}

export type MarketMark = "yes" | "no" | "void" | "pending";

/** What the viewer recorded for one market of an idea. */
export function marketMark(idea: Pick<SavedIdea, "outcomes" | "voids">, marketId: string): MarketMark {
  if (idea.voids?.includes(marketId)) return "void";
  return idea.outcomes[marketId] ?? "pending";
}

export type LegResult = "won" | "lost" | "void" | "pending";

/** One leg's paper result from the recorded market outcome: its side happened (won), the other did (lost), void, or not yet. */
export function legResult(idea: Pick<SavedIdea, "outcomes" | "voids">, leg: SlipLeg): LegResult {
  const m = marketMark(idea, leg.market.id);
  if (m === "pending" || m === "void") return m;
  return m === leg.quote.side ? "won" : "lost";
}

export type IdeaResult =
  | { readonly kind: "net"; readonly row: SlipRow; readonly totalCost: number }
  | { readonly kind: "open"; readonly recorded: number; readonly markets: number }
  | { readonly kind: "void"; readonly text: string }
  | { readonly kind: "incomplete"; readonly text: string };

export const VOID_TEXT = "A market recorded as void is refunded by each venue's own rules, which this app does not model, so no net is computed.";

/**
 * The paper net at the idea's SAVED prices for the outcomes the viewer entered: the matching row of evaluateDraft on the
 * idea's own snapshot (payout minus the saved total cost). Nothing current is read; a quote refresh cannot change it.
 */
export function ideaResult(idea: SavedIdea, evaluation: SlipEvaluation): IdeaResult {
  const ids = ideaMarketIds(idea);
  const marks = ids.map((id) => marketMark(idea, id));
  if (marks.includes("void")) return { kind: "void", text: VOID_TEXT };
  const recorded = marks.filter((m) => m !== "pending").length;
  if (!evaluation.complete || evaluation.totalCost === null) return { kind: "incomplete", text: evaluation.warnings[0] ?? "This saved idea is incomplete, so no net can be computed." };
  if (recorded < ids.length) return { kind: "open", recorded, markets: ids.length };
  const row = evaluation.rows.find((r) => ids.every((id) => r.outcomes[id] === idea.outcomes[id]));
  if (!row) return { kind: "incomplete", text: evaluation.warnings[0] ?? "No listed outcome matches the outcomes entered." };
  return { kind: "net", row, totalCost: evaluation.totalCost };
}

/** Evaluates an idea at its saved prices (the clock only affects the coverage gate, which the idea screens do not claim). */
export function evaluateIdea(idea: SavedIdea, now: number): SlipEvaluation {
  return evaluateDraft(idea, { now });
}

/** The sum of paper nets over settled ideas that have one, with how many there are (n). Voided ideas are not counted. */
export function paperTotal(ideas: readonly SavedIdea[], now: number): { net: number; n: number; excluded: number } {
  let net = 0;
  let n = 0;
  let excluded = 0;
  for (const i of ideas.filter((x) => x.status === "settled")) {
    const r = ideaResult(i, evaluateIdea(i, now));
    if (r.kind === "net") {
      net += r.row.net;
      n += 1;
    } else excluded += 1;
  }
  return { net: Math.round(net * 1e6) / 1e6, n, excluded };
}

/** Players, venues and the number of included legs, for a list row. */
export function ideaSummary(idea: SavedIdea): { players: string[]; venues: string[]; included: number } {
  const active = activeLegs(idea);
  return { players: [...new Set(idea.legs.map((l) => l.market.playerName))], venues: [...new Set(idea.legs.map((l) => l.quote.provider))], included: active.length };
}

// ------------------------------------------------------------------------------------------------ the ideas file

export const IDEAS_FORMAT = "parlayhitters-saved-ideas";
export const IDEAS_NOTE = "Saved ideas from one browser: frozen leg and price snapshots, the thesis, and outcomes typed by hand (unverified). Nothing here was placed at any venue, and no result is verified.";

export function ideasFile(ideas: readonly SavedIdea[], exportedAt: string): string {
  return JSON.stringify({ format: IDEAS_FORMAT, version: 1, exportedAt, note: IDEAS_NOTE, ideas }, null, 2);
}

export function ideasFilename(exportedAt: string, one?: SavedIdea): string {
  const day = exportedAt.slice(0, 10);
  if (!one) return `parlayhitters-ideas-${day}.json`;
  const slug = one.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "idea";
  return `parlayhitters-idea-${slug}-${day}.json`;
}

export type IdeasMerge = { ok: true; workspace: SlipWorkspace; added: number; skipped: number } | { ok: false; code: "invalid_data" | "import_too_large"; text: string };

/**
 * Adds the ideas in an ideas file to the workspace. Every incoming idea is checked by the workspace's own strict
 * validation; one bad idea refuses the whole file and nothing changes. An idea whose id is already here is skipped (never
 * overwritten). The slip, typed prices and picks are untouched.
 */
export function mergeIdeasFile(ws: SlipWorkspace, text: string): IdeasMerge {
  if (text.length > MAX_IMPORT_CHARS) return { ok: false, code: "import_too_large", text: "That file is too large to import. Nothing was changed." };
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return { ok: false, code: "invalid_data", text: "That file is not JSON. Nothing was changed." };
  }
  if (typeof doc !== "object" || doc === null || (doc as Record<string, unknown>)["format"] !== IDEAS_FORMAT) {
    return { ok: false, code: "invalid_data", text: "That file is not a parlayhitters ideas file. Nothing was changed. (A whole-workspace file is imported from the betting tools page.)" };
  }
  const d = doc as Record<string, unknown>;
  if (d["version"] !== 1) return { ok: false, code: "invalid_data", text: "That ideas file is from a version this app does not read. Nothing was changed." };
  const incoming = d["ideas"];
  if (!Array.isArray(incoming)) return { ok: false, code: "invalid_data", text: "That ideas file has no list of ideas. Nothing was changed." };
  const have = new Set(ws.ideas.map((i) => i.id));
  const fresh = (incoming as unknown[]).filter((i) => !(typeof i === "object" && i !== null && have.has(String((i as Record<string, unknown>)["id"]))));
  const check = validateSlipWorkspace({ draft: emptyDraft(), ideas: fresh, manualQuotes: [], picks: [] });
  if (!check.ok) return { ok: false, code: "invalid_data", text: `That ideas file did not pass its check (${check.text}). Nothing was changed.` };
  const all = validateSlipWorkspace({ ...ws, ideas: [...ws.ideas, ...check.workspace.ideas] });
  if (!all.ok) return { ok: false, code: "invalid_data", text: `Those ideas cannot be added here (${all.text}). Nothing was changed.` };
  return { ok: true, workspace: all.workspace, added: fresh.length, skipped: incoming.length - fresh.length };
}
