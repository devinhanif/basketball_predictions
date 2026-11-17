// The rolling slip: legs, planned vs recorded positions, per-provider native units, normalised cost per $1 of gross
// payout, the outcome table, and the gate that decides whether a coverage summary may be shown at all. Every number is
// arithmetic on prices the viewer typed or the export supplied. Nothing estimates a probability, correlation or edge;
// shared players and games are described in words only.
import { checkMarket, checkQuote, DEFAULT_MAX_QUOTE_AGE_MS, freshness, isFiniteIn, isId, isInstant, isObject, isSide, isText } from "./catalog";
import { enumerateOutcomes, grossPayoutOf, MAX_ENUMERATED_MARKETS, round6, sizeHedge, validNativeAmount, type OutcomeMap } from "./hedgeMath";
import { instantMs } from "../time";
import { deepFreeze, frozenCopy, jsonCopy, localId } from "./ids";
import type { EntryTerms, IdeaStatus, Market, ProviderId, Quote, Result, SavedIdea, Side, SlipDraft, SlipLeg, SlipWorkspace } from "./types";

export const MAX_LEGS = 64;
export const MAX_IDEAS = 500;
export const MAX_TAGS = 12;
export const MAX_MANUAL_QUOTES = 500;
export const ENTRY_MULTIPLIER_MAX = 100;

// ------------------------------------------------------------------------------------------------ prices

export type NormalisedCost =
  | { readonly ok: true; readonly cents: number }
  | { readonly ok: false; readonly code: "entry_only" | "fee_unknown" | "invalid_price"; readonly text: string };

/**
 * All-in cents paid for $1 of gross payout, fee included. Exchange: ask + fee. Sportsbook: 100 / (odds + 100) dollars for
 * positive odds, |odds| / (|odds| + 100) for negative, plus any fee per $1 of payout. An entry pick has no price of its
 * own, and an unknown fee blocks the number rather than counting as zero.
 */
export function normalisedCost(quote: Quote): NormalisedCost {
  const q = checkQuote(quote);
  if (!q.ok) return { ok: false, code: "invalid_price", text: `No cost shown, because the quote is invalid: ${q.reason}.` };
  const p = q.value.price;
  if (p.kind === "entry-pick") return { ok: false, code: "entry_only", text: "No per-pick price: the entry's payout rule prices the whole entry." };
  if (p.fee.status === "unknown") return { ok: false, code: "fee_unknown", text: "No cost shown, because this provider's fee is unknown." };
  if (p.kind === "binary") return { ok: true, cents: round6(p.askCents + p.fee.centsPerDollarPayout) };
  const o = p.americanOdds;
  return { ok: true, cents: round6((o > 0 ? 10000 / (o + 100) : (-o * 100) / (-o + 100)) + p.fee.centsPerDollarPayout) };
}

/** Why a leg cannot be priced, or null when it can. */
export function legProblem(leg: SlipLeg): string | null {
  const q = checkQuote(leg.quote);
  if (!q.ok) return `invalid quote: ${q.reason}`;
  const p = leg.quote.price;
  if (p.kind === "entry-pick") return leg.amount === null ? null : "an entry pick has no amount of its own";
  if (leg.amount === null || !validNativeAmount(p, leg.amount)) {
    return p.kind === "binary" ? "contracts must be a whole number from 1 to 1,000,000" : "stake must be $0.01 to $1,000,000, to the cent";
  }
  if (p.fee.status === "unknown") return "fee unknown";
  return null;
}

/** Dollars paid for this leg alone (fee included), or null: invalid, fee unknown, or an entry pick (priced per entry). */
export function legCost(leg: SlipLeg): number | null {
  if (legProblem(leg) !== null || leg.amount === null) return null;
  const p = leg.quote.price;
  if (p.kind === "binary" && p.fee.status === "known") return round6((leg.amount * (p.askCents + p.fee.centsPerDollarPayout)) / 100);
  if (p.kind === "sportsbook" && p.fee.status === "known") {
    const payout = grossPayoutOf(leg);
    return payout === null ? null : round6(leg.amount + (payout * p.fee.centsPerDollarPayout) / 100);
  }
  return null;
}

/** Gross dollars returned if this leg alone wins (stake included at a sportsbook), or null (entry pick, invalid). */
export function legPayout(leg: SlipLeg): number | null {
  return grossPayoutOf(leg);
}

// ------------------------------------------------------------------------------------------------ building a draft

/** Default native amount when a leg is added: 2 contracts, a $10 stake, or none for an entry pick. */
export function defaultAmount(quote: Quote): number | null {
  return quote.price.kind === "binary" ? 2 : quote.price.kind === "sportsbook" ? 10 : null;
}

/** A new leg holding frozen copies of the quote and market as they are now. */
export function makeLeg(quote: Quote, market: Market, role: SlipLeg["role"] = "planned", opts: { id?: string; amount?: number | null; placedAt?: string | null } = {}): SlipLeg {
  return {
    id: opts.id ?? localId("leg"),
    quote: frozenCopy(quote),
    market: frozenCopy(market),
    amount: opts.amount !== undefined ? opts.amount : defaultAmount(quote),
    enabled: true,
    role,
    placedAt: role === "existing" ? (opts.placedAt ?? null) : null,
  };
}

export const DEFAULT_ENTRY: EntryTerms = Object.freeze({ stake: 10, multiplier: 3 });

export function emptyDraft(): SlipDraft {
  return { legs: [], entry: { ...DEFAULT_ENTRY }, title: "", thesis: "", tags: [], origin: "user" };
}

/** An empty workspace. Nothing is seeded: the sample loads only through `sample.ts`, on an explicit action. */
export function createSlipWorkspace(): SlipWorkspace {
  return { draft: emptyDraft(), ideas: [], manualQuotes: [] };
}

/** Recorded positions always count; planned legs count only while switched on. */
export function activeLegs(draft: Pick<SlipDraft, "legs">): SlipLeg[] {
  return draft.legs.filter((l) => l.role === "existing" || l.enabled);
}

export type DraftEdit = Result<{ draft: SlipDraft }, "entry_same_market" | "too_many_legs" | "not_found" | "not_planned" | "invalid_amount">;

/**
 * Adds a planned leg for this quote. If a planned leg already holds the same quote id it is switched back on (taking the
 * quote the viewer just chose) instead of being duplicated.
 */
export function addQuote(draft: SlipDraft, quote: Quote, market: Market, opts: { id?: string; amount?: number | null } = {}): DraftEdit {
  const same = draft.legs.find((l) => l.role === "planned" && l.quote.id === quote.id);
  if (same) {
    const leg: SlipLeg = { ...same, quote: frozenCopy(quote), market: frozenCopy(market), enabled: true };
    return { ok: true, draft: { ...draft, legs: draft.legs.map((l) => (l.id === same.id ? leg : l)) } };
  }
  if (quote.price.kind === "entry-pick" && draft.legs.some((l) => l.quote.price.kind === "entry-pick" && l.quote.marketId === quote.marketId)) {
    return { ok: false, code: "entry_same_market", text: "The entry already has a pick in this market. Remove that pick before adding this one." };
  }
  if (draft.legs.length >= MAX_LEGS) return { ok: false, code: "too_many_legs", text: `A slip holds at most ${MAX_LEGS} legs.` };
  return { ok: true, draft: { ...draft, legs: [...draft.legs, makeLeg(quote, market, "planned", opts)] } };
}

/** Removes a leg. For a recorded position this removes the local record only; it does not touch any real wager. */
export function removeLeg(draft: SlipDraft, legId: string): SlipDraft {
  return { ...draft, legs: draft.legs.filter((l) => l.id !== legId) };
}

/** Switches a planned leg on or off. Recorded positions cannot be switched off: they stay in exposure. */
export function toggleLeg(draft: SlipDraft, legId: string): DraftEdit {
  const leg = draft.legs.find((l) => l.id === legId);
  if (!leg) return { ok: false, code: "not_found", text: "That leg is not in the slip." };
  if (leg.role !== "planned") return { ok: false, code: "not_planned", text: "A recorded position stays in exposure; remove the record instead." };
  return { ok: true, draft: { ...draft, legs: draft.legs.map((l) => (l.id === legId ? { ...l, enabled: !l.enabled } : l)) } };
}

/** Sets a leg's native amount (contracts or stake); refuses an amount the provider's unit cannot take. */
export function setLegAmount(draft: SlipDraft, legId: string, amount: number): DraftEdit {
  const leg = draft.legs.find((l) => l.id === legId);
  if (!leg) return { ok: false, code: "not_found", text: "That leg is not in the slip." };
  if (!validNativeAmount(leg.quote.price, amount)) {
    return { ok: false, code: "invalid_amount", text: leg.quote.price.kind === "binary" ? "Use a whole number of contracts." : leg.quote.price.kind === "sportsbook" ? "Use a stake to the cent." : "An entry pick has no amount; set the entry stake." };
  }
  return { ok: true, draft: { ...draft, legs: draft.legs.map((l) => (l.id === legId ? { ...l, amount } : l)) } };
}

export function setEntry(draft: SlipDraft, entry: EntryTerms): SlipDraft {
  return { ...draft, entry: { stake: entry.stake, multiplier: entry.multiplier } };
}

/**
 * Records the switched-on planned legs as already placed elsewhere: each keeps its id, quote snapshot, side and amount and
 * gains `placedAt`. Switched-off planned legs stay planned. A local record only; nothing is sent anywhere.
 */
export function markPlaced(draft: SlipDraft, placedAt: string): SlipDraft {
  return { ...draft, legs: draft.legs.map((l) => (l.role === "planned" && l.enabled ? { ...l, role: "existing" as const, enabled: true, placedAt } : l)) };
}

/** A planned hedge leg on this quote, sized to the uncovered gross payout of the recorded positions in its market. */
export function makeHedgeLeg(draft: SlipDraft, quote: Quote, market: Market, opts: { id?: string } = {}): Result<{ leg: SlipLeg; sized: boolean; text: string }> {
  const size = sizeHedge(draft.legs, market.id, quote.side, quote.price);
  if (!size.ok && size.code !== "nothing_to_cover") return size;
  const leg = makeLeg(quote, market, "planned", { ...opts, ...(size.ok ? { amount: size.amount } : {}) });
  return size.ok
    ? { ok: true, leg, sized: true, text: size.unit === "contracts" ? `${size.amount} contracts cover $${size.grossPayout.toFixed(2)} gross payout.` : `A $${size.amount.toFixed(2)} stake covers $${size.grossPayout.toFixed(2)} gross payout.` }
    : { ok: true, leg, sized: false, text: size.text };
}

// ------------------------------------------------------------------------------------------------ overlaps (words only)

export interface SlipOverlap {
  readonly kind: "same-market" | "same-player" | "same-event";
  readonly marketIds: readonly string[];
  readonly label: string;
  readonly detail: string;
}

/** Shared exposure described in words: same market, same player, same game. Never a correlation or a score. */
export function describeOverlaps(legs: readonly SlipLeg[]): SlipOverlap[] {
  const active = activeLegs({ legs });
  const out: SlipOverlap[] = [];
  const markets = uniqueMarkets(active);
  for (const m of markets) {
    const matched = active.filter((l) => l.quote.marketId === m.id);
    if (matched.length < 2) continue;
    const opposite = new Set(matched.map((l) => l.quote.side)).size > 1;
    out.push({
      kind: "same-market",
      marketIds: [m.id],
      label: opposite ? "Opposite sides, same line" : "Repeated market exposure",
      detail: opposite
        ? `${m.playerName}: YES and NO share one outcome here. What each pays depends on the amounts and each provider's rules.`
        : `${m.playerName}: ${matched.length} legs win or lose together; they are not spread out.`,
    });
  }
  const players = new Map<string, Market[]>();
  for (const m of markets) players.set(`${m.eventId}|${m.playerId}`, [...(players.get(`${m.eventId}|${m.playerId}`) ?? []), m]);
  for (const ms of players.values()) {
    if (ms.length < 2) continue;
    const first = ms[0];
    if (first === undefined) continue;
    out.push({ kind: "same-player", marketIds: ms.map((m) => m.id), label: `Same player · ${first.playerName}`, detail: "These markets are about one player in one game. Every YES/NO combination is listed; no relationship between them is assumed." });
  }
  const events = new Map<string, Market[]>();
  for (const m of markets) events.set(m.eventId, [...(events.get(m.eventId) ?? []), m]);
  for (const ms of events.values()) {
    if (ms.length < 2) continue;
    const first = ms[0];
    if (first === undefined) continue;
    out.push({ kind: "same-event", marketIds: ms.map((m) => m.id), label: `Shared game · ${first.eventLabel || first.eventId}`, detail: "These markets share a game. Every YES/NO combination is listed; no relationship or probability is assumed." });
  }
  return out;
}

function uniqueMarkets(legs: readonly SlipLeg[]): Market[] {
  const seen = new Map<string, Market>();
  for (const l of legs) if (!seen.has(l.market.id)) seen.set(l.market.id, l.market);
  return [...seen.values()];
}

// ------------------------------------------------------------------------------------------------ evaluation

export interface SlipRow {
  readonly id: string;
  readonly label: string;
  readonly outcomes: OutcomeMap;
  readonly payout: number;
  readonly net: number;
  readonly existingNet: number | null;
}

export interface SlipGroup {
  readonly provider: ProviderId;
  readonly kind: "exchange" | "sportsbook" | "entry";
  readonly legs: readonly SlipLeg[];
  /** Null when a leg's cost is unknown (fee unknown, invalid amount). */
  readonly cost: number | null;
  readonly maxPayout: number | null;
  readonly complete: boolean;
  readonly note: string;
}

export type CoverageGap = "no_legs" | "incomplete" | "too_many_markets" | "stale_quote" | "unverified_settlement" | "related_markets";

/**
 * The only place a coverage summary (floor, ceiling, "every listed outcome is positive") may come from. It covers the
 * listed YES/NO outcomes only, under the entered prices; when any input is stale, incomplete or unverified it is not shown.
 */
export type Coverage =
  | {
      readonly shown: true;
      readonly worst: number;
      readonly best: number;
      readonly allListedPositive: boolean;
      /** Null when there are no recorded positions or no planned legs to compare. */
      readonly improvesOnExisting: boolean | null;
      readonly basis: "entered_prices" | "sample_prices";
    }
  | { readonly shown: false; readonly reason: CoverageGap; readonly text: string };

export interface SlipEvaluation {
  readonly complete: boolean;
  /** Null when any active cost is unknown. */
  readonly totalCost: number | null;
  readonly providerTotals: Readonly<Record<ProviderId, number | null>>;
  readonly groups: readonly SlipGroup[];
  /** The outcome table (arithmetic on entered prices); empty unless complete and within the enumeration cap. */
  readonly rows: readonly SlipRow[];
  /** Worst net of the recorded positions alone (entry prices are fixed facts), or null when they cannot be evaluated. */
  readonly existingWorst: number | null;
  readonly coverage: Coverage;
  readonly warnings: readonly string[];
  readonly overlaps: readonly SlipOverlap[];
}

function entryCheck(picks: readonly SlipLeg[], entry: EntryTerms): string[] {
  if (!picks.length) return [];
  const notes: string[] = [];
  if (picks.length < 2) notes.push("An entry needs at least two picks in different markets before it can be evaluated.");
  if (new Set(picks.map((l) => l.quote.marketId)).size !== picks.length) notes.push("An entry must use different markets; duplicate or opposing picks in the same market are incomplete.");
  if (new Set(picks.map((l) => l.role)).size > 1) notes.push("Entry picks share one entry. Keep the whole entry planned or the whole entry recorded.");
  if (new Set(picks.map((l) => l.quote.provider)).size > 1) notes.push("Entry picks from different providers are different entries; this slip holds one entry.");
  if (!isFiniteIn(entry.stake, 0.01, 1_000_000)) notes.push("Enter a positive entry stake.");
  if (!isFiniteIn(entry.multiplier, 1, ENTRY_MULTIPLIER_MAX)) notes.push(`Enter an all-correct gross multiplier from 1x to ${ENTRY_MULTIPLIER_MAX}x.`);
  return notes;
}

function payoutFor(legs: readonly SlipLeg[], entry: EntryTerms, outcomes: OutcomeMap): number {
  const picks = legs.filter((l) => l.quote.price.kind === "entry-pick");
  let payout = 0;
  for (const l of legs) if (l.quote.price.kind !== "entry-pick" && outcomes[l.quote.marketId] === l.quote.side) payout += legPayout(l) ?? 0;
  if (picks.length >= 2 && picks.every((l) => outcomes[l.quote.marketId] === l.quote.side)) payout += entry.stake * entry.multiplier;
  return round6(payout);
}

function costFor(legs: readonly SlipLeg[], entry: EntryTerms): number | null {
  let total = 0;
  for (const l of legs) {
    if (l.quote.price.kind === "entry-pick") continue;
    const c = legCost(l);
    if (c === null) return null;
    total += c;
  }
  if (legs.some((l) => l.quote.price.kind === "entry-pick")) {
    if (!isFiniteIn(entry.stake, 0, 1_000_000)) return null;
    total += entry.stake;
  }
  return round6(total);
}

const KIND_NOTE = {
  exchange: "Amount is whole contracts. Each winning contract returns $1; ask plus fee is paid per contract.",
  sportsbook: "Amount is the stake. Gross return includes the stake; any fee is per $1 of payout.",
  entry: "One grouped, all-correct entry. The multiplier includes the returned stake.",
} as const;

const kindOf = (l: SlipLeg): SlipGroup["kind"] => (l.quote.price.kind === "binary" ? "exchange" : l.quote.price.kind === "sportsbook" ? "sportsbook" : "entry");

function rowLabel(markets: readonly Market[], outcomes: OutcomeMap): string {
  const last = (m: Market): string => m.playerName.trim().split(/\s+/).pop() ?? m.playerName;
  const unique = new Set(markets.map(last)).size === markets.length;
  return markets.map((m) => `${unique ? last(m) : `${last(m)} ${m.stat} ${m.line}`} ${(outcomes[m.id] ?? "no").toUpperCase()}`).join(" · ");
}

export interface EvaluateOptions {
  /** Milliseconds since epoch: the time freshness is judged at. Required: a stored clock is never assumed. */
  readonly now: number;
  readonly maxQuoteAgeMs?: number;
  readonly maxMarkets?: number;
}

/**
 * The slip's cost, outcome table and coverage gate. The table lists every YES/NO combination of the markets involved
 * (bounded by `maxMarkets`); it is arithmetic, not a probability, and it says nothing about which outcome is likely.
 */
export function evaluateDraft(draft: SlipDraft, opts: EvaluateOptions): SlipEvaluation {
  const legs = activeLegs(draft);
  const warnings: string[] = [];
  const problems = legs.map(legProblem).filter((p): p is string => p !== null);
  if (!legs.length) warnings.push("Add a planned leg or a recorded position to evaluate the slip.");
  if (problems.some((p) => p === "fee unknown")) warnings.push("A provider fee is unknown, so cost and outcomes are not shown. Enter the fee to continue.");
  if (problems.some((p) => p !== "fee unknown")) warnings.push("One or more active legs has an invalid amount, price or odds. Fix it before evaluating outcomes.");
  const picks = legs.filter((l) => l.quote.price.kind === "entry-pick");
  const entryNotes = entryCheck(picks, draft.entry);
  warnings.push(...entryNotes);
  if (picks.length) warnings.push("The entry uses one all-correct multiplier, stake included. Eligibility, pushes, voids, flex payouts and promotions are not modelled.");
  if (legs.some((l) => l.quote.provenance === "sample")) warnings.push("Sample prices only. They are not live or verified.");
  if (legs.some((l) => l.quote.provenance === "manual")) warnings.push("Manual prices are what you typed: assumptions, not verified fills or a live feed.");
  if (new Set(legs.map((l) => l.quote.provider)).size > 1) warnings.push("Cross-provider settlement and void rules must match. Matching player names and lines alone do not prove it.");
  const complete = legs.length > 0 && problems.length === 0 && entryNotes.length === 0;

  const providers = [...new Set(legs.map((l) => l.quote.provider))];
  const groups: SlipGroup[] = providers.map((provider) => {
    const grouped = legs.filter((l) => l.quote.provider === provider);
    const kind = kindOf(grouped[0] as SlipLeg);
    const groupComplete = grouped.every((l) => legProblem(l) === null) && (kind !== "entry" || entryCheck(grouped, draft.entry).length === 0);
    const cost = kind === "entry" ? (isFiniteIn(draft.entry.stake, 0, 1_000_000) ? round6(draft.entry.stake) : null) : costFor(grouped, draft.entry);
    let maxPayout: number | null = null;
    if (groupComplete) {
      const e = enumerateOutcomes(uniqueMarkets(grouped).map((m) => m.id), opts.maxMarkets ?? MAX_ENUMERATED_MARKETS);
      if (e.ok) maxPayout = Math.max(...e.outcomes.map((o) => payoutFor(grouped, draft.entry, o)));
    }
    return { provider, kind, legs: grouped, cost, maxPayout, complete: groupComplete, note: KIND_NOTE[kind] };
  });
  const providerTotals: Record<ProviderId, number | null> = {};
  for (const g of groups) providerTotals[g.provider] = g.cost;
  const totalCost = groups.every((g) => g.cost !== null) ? round6(groups.reduce((s, g) => s + (g.cost ?? 0), 0)) : null;

  const markets = uniqueMarkets(legs);
  const enumeration = enumerateOutcomes(markets.map((m) => m.id), opts.maxMarkets ?? MAX_ENUMERATED_MARKETS);
  if (!enumeration.ok) warnings.push(enumeration.text);
  const combos = enumeration.ok ? enumeration.outcomes : [];
  const existing = legs.filter((l) => l.role === "existing");
  const existingValid = enumeration.ok && existing.every((l) => legProblem(l) === null) && entryCheck(existing.filter((l) => l.quote.price.kind === "entry-pick"), draft.entry).length === 0;
  const existingCost = existingValid ? costFor(existing, draft.entry) : null;
  const existingWorst = existingCost !== null ? round6(Math.min(...combos.map((o) => payoutFor(existing, draft.entry, o) - existingCost))) : null;
  const rows: SlipRow[] =
    complete && enumeration.ok && totalCost !== null
      ? combos.map((outcomes, i) => {
          const payout = payoutFor(legs, draft.entry, outcomes);
          return { id: `scenario-${i}`, label: rowLabel(markets, outcomes), outcomes, payout, net: round6(payout - totalCost), existingNet: existingCost !== null ? round6(payoutFor(existing, draft.entry, outcomes) - existingCost) : null };
        })
      : [];

  const coverage = coverageGate({ legs, markets, complete, enumerationOk: enumeration.ok, enumerationText: enumeration.ok ? "" : enumeration.text, rows, existing, existingWorst, opts });
  return { complete, totalCost, providerTotals, groups, rows, existingWorst, coverage, warnings, overlaps: describeOverlaps(legs) };
}

function coverageGate(x: {
  legs: readonly SlipLeg[];
  markets: readonly Market[];
  complete: boolean;
  enumerationOk: boolean;
  enumerationText: string;
  rows: readonly SlipRow[];
  existing: readonly SlipLeg[];
  existingWorst: number | null;
  opts: EvaluateOptions;
}): Coverage {
  const no = (reason: CoverageGap, text: string): Coverage => ({ shown: false, reason, text });
  if (!x.legs.length) return no("no_legs", "Not shown, because the slip is empty.");
  if (!x.enumerationOk) return no("too_many_markets", x.enumerationText);
  if (!x.complete || !x.rows.length) return no("incomplete", "Not shown, because a leg or the entry is incomplete or has an unknown fee.");
  const planned = x.legs.filter((l) => l.role === "planned");
  const stale = planned.filter((l) => {
    const f = freshness(l.quote, x.opts.now, x.opts.maxQuoteAgeMs ?? DEFAULT_MAX_QUOTE_AGE_MS);
    return f === "stale" || f === "unknown";
  });
  if (stale.length) return no("stale_quote", `Not shown, because ${stale.length === 1 ? "a planned price is" : `${stale.length} planned prices are`} out of date or of unknown age. Refresh or re-enter it.`);
  for (const m of x.markets) {
    const onMarket = x.legs.filter((l) => l.market.id === m.id);
    const rules = new Set(onMarket.map((l) => l.market.settlementRuleId));
    if (new Set(onMarket.map((l) => l.quote.provider)).size > 1 && (rules.has(null) || rules.size > 1)) {
      return no("unverified_settlement", `Not shown, because ${m.playerName}'s market is on more than one provider and their settlement rules (DNP, void, overtime) are not verified to match.`);
    }
  }
  const lines = new Map<string, Set<number>>();
  for (const m of x.markets) {
    const k = [m.eventId, m.playerId, m.stat, m.period].join("|");
    lines.set(k, (lines.get(k) ?? new Set<number>()).add(m.line));
  }
  if ([...lines.values()].some((s) => s.size > 1)) {
    return no("related_markets", "Not shown, because two lines on the same player and stat are listed as if independent; some listed combinations cannot happen.");
  }
  const nets = x.rows.map((r) => r.net);
  const worst = Math.min(...nets);
  const best = Math.max(...nets);
  const improves = x.existing.length > 0 && planned.length > 0 && x.existingWorst !== null ? worst > x.existingWorst : null;
  return { shown: true, worst, best, allListedPositive: worst > 0, improvesOnExisting: improves, basis: x.legs.some((l) => l.quote.provenance === "sample") ? "sample_prices" : "entered_prices" };
}

// ------------------------------------------------------------------------------------------------ ideas

export type IdeaEdit = Result<{ workspace: SlipWorkspace }, "empty_draft" | "too_many_ideas" | "not_found" | "outcomes_incomplete" | "invalid_quote">;

/** Saves the draft as an independent frozen idea; later edits to the draft or the board never reach it. */
export function saveIdea(ws: SlipWorkspace, opts: { now: string; status?: Exclude<IdeaStatus, "settled">; id?: string; title?: string }): IdeaEdit {
  const included = activeLegs(ws.draft);
  if (!included.length) return { ok: false, code: "empty_draft", text: "Add at least one leg before saving an idea." };
  if (ws.ideas.length >= MAX_IDEAS) return { ok: false, code: "too_many_ideas", text: `At most ${MAX_IDEAS} saved ideas.` };
  const title = (opts.title ?? ws.draft.title).trim() || `${[...new Set(included.map((l) => l.market.playerName))].join(" + ")} · ${included.length} legs`;
  const idea: SavedIdea = frozenCopy({ ...ws.draft, title: title.slice(0, 200), id: opts.id ?? localId("idea"), createdAt: opts.now, status: opts.status ?? "watching", outcomes: {} });
  return { ok: true, workspace: { ...ws, ideas: [...ws.ideas, idea] } };
}

/** Copies an idea into the working draft; the saved idea stays as it was. */
export function loadIdea(ws: SlipWorkspace, ideaId: string): IdeaEdit {
  const idea = ws.ideas.find((i) => i.id === ideaId);
  if (!idea) return { ok: false, code: "not_found", text: "That idea is not saved here." };
  const copy = jsonCopy(idea);
  const draft: SlipDraft = { legs: copy.legs.map((l) => ({ ...l, quote: deepFreeze(l.quote), market: deepFreeze(l.market) })), entry: copy.entry, title: copy.title, thesis: copy.thesis, tags: copy.tags, origin: copy.origin };
  return { ok: true, workspace: { ...ws, draft } };
}

export function removeIdea(ws: SlipWorkspace, ideaId: string): IdeaEdit {
  if (!ws.ideas.some((i) => i.id === ideaId)) return { ok: false, code: "not_found", text: "That idea is not saved here." };
  return { ok: true, workspace: { ...ws, ideas: ws.ideas.filter((i) => i.id !== ideaId) } };
}

/** The markets an idea's outcome must cover: every active leg's market. */
export function ideaMarketIds(idea: Pick<SlipDraft, "legs">): string[] {
  return uniqueMarkets(activeLegs(idea)).map((m) => m.id);
}

/**
 * Records the viewer's own outcome for an idea (a paper result, never a provider settlement). "settled" needs an outcome
 * for every active market; "paper" or "watching" reopens it and may clear them.
 */
export function recordOutcomes(ws: SlipWorkspace, ideaId: string, outcomes: Readonly<Record<string, Side>>, status: IdeaStatus): IdeaEdit {
  const idea = ws.ideas.find((i) => i.id === ideaId);
  if (!idea) return { ok: false, code: "not_found", text: "That idea is not saved here." };
  const ids = ideaMarketIds(idea);
  const clean: Record<string, Side> = {};
  for (const id of ids) {
    const s = outcomes[id];
    if (s !== undefined) clean[id] = s;
  }
  if (status === "settled" && Object.keys(clean).length !== ids.length) return { ok: false, code: "outcomes_incomplete", text: "Record an outcome for every market in the idea before settling it." };
  const next: SavedIdea = frozenCopy({ ...idea, status, outcomes: clean });
  return { ok: true, workspace: { ...ws, ideas: ws.ideas.map((i) => (i.id === ideaId ? next : i)) } };
}

/** Stores a price the viewer typed as a manual snapshot (replacing any earlier one in the same slot). Legs are untouched. */
export function putManualQuote(ws: SlipWorkspace, raw: unknown): IdeaEdit {
  const q = checkQuote(raw);
  if (!q.ok) return { ok: false, code: "invalid_quote", text: `That price was not saved: ${q.reason}.` };
  if (q.value.provenance !== "manual") return { ok: false, code: "invalid_quote", text: "A typed price must be marked manual." };
  const v = q.value;
  const rest = ws.manualQuotes.filter((m) => m.id !== v.id && !(m.marketId === v.marketId && m.provider === v.provider && m.side === v.side));
  if (rest.length >= MAX_MANUAL_QUOTES) return { ok: false, code: "too_many_ideas", text: `At most ${MAX_MANUAL_QUOTES} typed prices.` };
  return { ok: true, workspace: { ...ws, manualQuotes: [...rest, frozenCopy(v)] } };
}

// ------------------------------------------------------------------------------------------------ validation

type Check<T> = { ok: true; value: T } | { ok: false; path: string; reason: string };

function checkLeg(v: unknown, path: string): Check<SlipLeg> {
  const bad = (reason: string, sub = ""): Check<SlipLeg> => ({ ok: false, path: `${path}${sub}`, reason });
  if (!isObject(v)) return bad("not an object");
  if (!isId(v["id"])) return bad("id missing", ".id");
  const q = checkQuote(v["quote"]);
  if (!q.ok) return bad(q.reason, ".quote");
  const m = checkMarket(v["market"]);
  if (!m.ok) return bad(m.reason, ".market");
  if (q.value.marketId !== m.value.id) return bad("quote and market do not match", ".quote.marketId");
  const role = v["role"];
  if (role !== "planned" && role !== "existing") return bad("role must be planned or existing", ".role");
  if (typeof v["enabled"] !== "boolean") return bad("enabled must be true or false", ".enabled");
  const amount = v["amount"];
  if (q.value.price.kind === "entry-pick") {
    if (amount !== null) return bad("an entry pick has no amount", ".amount");
  } else if (typeof amount !== "number" || !validNativeAmount(q.value.price, amount)) return bad("invalid native amount", ".amount");
  const placedAt = v["placedAt"];
  if (role === "planned" && placedAt !== null) return bad("a planned leg has no placedAt", ".placedAt");
  if (role === "existing" && placedAt !== null && !isInstant(placedAt)) return bad("placedAt must be an ISO instant or null", ".placedAt");
  return {
    ok: true,
    value: { id: v["id"], quote: deepFreeze(q.value), market: deepFreeze(m.value), amount, enabled: role === "existing" ? true : v["enabled"], role, placedAt: placedAt as string | null },
  };
}

function checkDraft(v: unknown, path: string): Check<SlipDraft> {
  const bad = (reason: string, sub = ""): Check<SlipDraft> => ({ ok: false, path: `${path}${sub}`, reason });
  if (!isObject(v)) return bad("not an object");
  const rawLegs = v["legs"];
  if (!Array.isArray(rawLegs) || rawLegs.length > MAX_LEGS) return bad(`legs must be a list of at most ${MAX_LEGS}`, ".legs");
  const legs: SlipLeg[] = [];
  for (const [i, raw] of (rawLegs as unknown[]).entries()) {
    const r = checkLeg(raw, `${path}.legs[${i}]`);
    if (!r.ok) return r;
    legs.push(r.value);
  }
  if (new Set(legs.map((l) => l.id)).size !== legs.length) return bad("duplicate leg ids", ".legs");
  const entry = v["entry"];
  if (!isObject(entry) || !isFiniteIn(entry["stake"], 0.01, 1_000_000) || !isFiniteIn(entry["multiplier"], 1, ENTRY_MULTIPLIER_MAX)) return bad("entry stake or multiplier out of range", ".entry");
  if (!isText(v["title"], 200)) return bad("title must be text up to 200 characters", ".title");
  if (!isText(v["thesis"], 5000)) return bad("thesis must be text up to 5,000 characters", ".thesis");
  const tags = v["tags"];
  if (!Array.isArray(tags) || tags.length > MAX_TAGS || !(tags as unknown[]).every((t) => isText(t, 60))) return bad(`tags must be up to ${MAX_TAGS} short texts`, ".tags");
  const origin = v["origin"];
  if (origin !== "user" && origin !== "sample") return bad("origin must be user or sample", ".origin");
  return { ok: true, value: { legs, entry: { stake: entry["stake"], multiplier: entry["multiplier"] }, title: v["title"], thesis: v["thesis"], tags: [...(tags as string[])], origin } };
}

function checkIdea(v: unknown, path: string): Check<SavedIdea> {
  const d = checkDraft(v, path);
  if (!d.ok) return d;
  const o = v as Record<string, unknown>;
  const bad = (reason: string, sub: string): Check<SavedIdea> => ({ ok: false, path: `${path}${sub}`, reason });
  if (!isId(o["id"])) return bad("id missing", ".id");
  if (!isInstant(o["createdAt"])) return bad("createdAt must be an ISO instant", ".createdAt");
  const status = o["status"];
  if (status !== "watching" && status !== "paper" && status !== "settled") return bad("status must be watching, paper or settled", ".status");
  const outcomes = o["outcomes"];
  if (!isObject(outcomes)) return bad("outcomes must be an object", ".outcomes");
  const ids = ideaMarketIds(d.value);
  const allIds = new Set(d.value.legs.map((l) => l.market.id));
  for (const [k, s] of Object.entries(outcomes)) if (!allIds.has(k) || !isSide(s)) return bad(`outcome for ${k} is not a market in this idea or not yes/no`, ".outcomes");
  if (status === "settled") {
    if (!evaluateDraft(d.value, { now: instantMs(o["createdAt"]) }).complete) return bad("a settled idea must be complete", "");
    if (!ids.every((id) => isSide(outcomes[id]))) return bad("a settled idea needs an outcome for every active market", ".outcomes");
  }
  return { ok: true, value: { ...d.value, id: o["id"], createdAt: o["createdAt"], status, outcomes: { ...(outcomes as Record<string, Side>) } } };
}

export type WorkspaceCheck = Result<{ workspace: SlipWorkspace }, "invalid_data">;

/**
 * Strict validation of a whole stored or imported workspace. On success the result is an independent, exact-schema,
 * deep-frozen copy (unknown fields dropped). On failure it names the first bad path; it never substitutes sample data.
 */
export function validateSlipWorkspace(value: unknown): WorkspaceCheck {
  const fail = (path: string, reason: string): WorkspaceCheck => ({ ok: false, code: "invalid_data", text: `${path || "workspace"}: ${reason}` });
  if (!isObject(value)) return fail("", "not an object");
  const d = checkDraft(value["draft"], "draft");
  if (!d.ok) return fail(d.path, d.reason);
  const rawIdeas = value["ideas"];
  if (!Array.isArray(rawIdeas) || rawIdeas.length > MAX_IDEAS) return fail("ideas", `must be a list of at most ${MAX_IDEAS}`);
  const ideas: SavedIdea[] = [];
  for (const [i, raw] of (rawIdeas as unknown[]).entries()) {
    const r = checkIdea(raw, `ideas[${i}]`);
    if (!r.ok) return fail(r.path, r.reason);
    ideas.push(r.value);
  }
  if (new Set(ideas.map((i) => i.id)).size !== ideas.length) return fail("ideas", "duplicate idea ids");
  const rawQuotes = value["manualQuotes"];
  if (!Array.isArray(rawQuotes) || rawQuotes.length > MAX_MANUAL_QUOTES) return fail("manualQuotes", `must be a list of at most ${MAX_MANUAL_QUOTES}`);
  const manualQuotes: Quote[] = [];
  for (const [i, raw] of (rawQuotes as unknown[]).entries()) {
    const r = checkQuote(raw);
    if (!r.ok) return fail(`manualQuotes[${i}]`, r.reason);
    if (r.value.provenance !== "manual") return fail(`manualQuotes[${i}].provenance`, "a typed price must be manual");
    manualQuotes.push(r.value);
  }
  if (new Set(manualQuotes.map((q) => q.id)).size !== manualQuotes.length) return fail("manualQuotes", "duplicate quote ids");
  if (new Set(manualQuotes.map((q) => `${q.marketId}|${q.provider}|${q.side}`)).size !== manualQuotes.length) return fail("manualQuotes", "two typed prices for the same market, provider and side");
  return { ok: true, workspace: deepFreeze(jsonCopy({ draft: d.value, ideas, manualQuotes })) };
}

/** Type guard form of `validateSlipWorkspace`, for generic code. */
export function isSlipWorkspace(value: unknown): value is SlipWorkspace {
  return validateSlipWorkspace(value).ok;
}
