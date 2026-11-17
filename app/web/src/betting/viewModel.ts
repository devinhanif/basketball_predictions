// What the Compare, Hedge Finder and slip screens show, as plain data: the venue cards for one market and side, the
// typed-price form's result, the hash for a comparison, held positions and hedge previews. Every number comes from
// slipModel / hedgeMath / catalog; this file only arranges them. No probability, ranking by value or arbitrage claim.
import { comparability, freshness, oppositeSide, quoteSlot } from "./catalog";
import { addQuote, evaluateDraft, makeHedgeLeg, normalisedCost, type NormalisedCost, type SlipEvaluation } from "./slipModel";
import { byName } from "./snapshots";
import { localId } from "./ids";
import type { Catalog, Fee, Freshness, Market, ProviderInfo, Quote, Side, SlipDraft, SlipLeg, SlipWorkspace } from "./types";

// ------------------------------------------------------------------------------------------------ the address

export interface CompareQuery {
  game: string | null;
  player: string | null;
  stat: string | null;
  line: number | null;
  side: "over" | "under";
  /** A market held in the workspace (a typed custom market) by id. */
  market: string | null;
  /** Hedge Finder: the held leg to start from. */
  leg: string | null;
}

const ID = /^[A-Za-z0-9_.:-]{1,160}$/;

export function parseCompareQuery(query: string): CompareQuery {
  const p = new URLSearchParams(query);
  const id = (k: string): string | null => {
    const v = p.get(k);
    return v !== null && ID.test(v) ? v : null;
  };
  const lineRaw = p.get("line");
  const line = lineRaw !== null && /^\d{1,4}(\.\d{1,2})?$/.test(lineRaw) ? Number(lineRaw) : null;
  return { game: id("game"), player: id("player"), stat: id("stat"), line, side: p.get("side") === "under" ? "under" : "over", market: id("market"), leg: id("leg") };
}

/** The hash for Compare (or Hedge Finder) on one market and side. Parameters are plain ids and numbers. */
export function compareHash(path: "compare" | "hedge" | "analysis", q: Partial<CompareQuery>): string {
  const p = new URLSearchParams();
  if (q.market) p.set("market", q.market);
  else {
    if (q.game) p.set("game", q.game);
    if (q.player) p.set("player", q.player);
    if (q.stat) p.set("stat", q.stat);
    if (q.line !== null && q.line !== undefined) p.set("line", String(q.line));
  }
  if (q.side) p.set("side", q.side);
  if (q.leg) p.set("leg", q.leg);
  const s = p.toString();
  return `#/bet/${path}${s ? `?${s}` : ""}`;
}

/** The Compare address for a market (export and sample markets by their facts, a typed market by its id). */
export function compareHashFor(market: Market, side: Side, path: "compare" | "hedge" | "analysis" = "compare", leg: string | null = null): string {
  const s = side === "yes" ? "over" : "under";
  if (market.origin === "manual") return compareHash(path, { market: market.id, side: s, leg });
  return compareHash(path, { game: market.eventId, player: market.playerId, stat: market.stat, line: market.line, side: s, leg });
}

/** Every market the workspace holds (legs, picks, saved ideas), newest first, one per id. */
export function knownMarkets(ws: SlipWorkspace): Market[] {
  const out = new Map<string, Market>();
  for (const l of [...ws.draft.legs].reverse()) out.set(l.market.id, out.get(l.market.id) ?? l.market);
  for (const p of [...ws.picks].reverse()) out.set(p.market.id, out.get(p.market.id) ?? p.market);
  for (const i of ws.ideas) for (const l of i.legs) out.set(l.market.id, out.get(l.market.id) ?? l.market);
  return [...out.values()];
}

// ------------------------------------------------------------------------------------------------ typed prices

export interface PriceInput {
  /** Exchange: ask in cents. Sportsbook: American odds. Entry: ignored. */
  price: string;
  /** Cents per $1 of payout; blank means unknown. */
  fee: string;
}

export type TypedQuote = { ok: true; quote: Quote } | { ok: false; text: string };

/**
 * A quote from what the viewer typed, in the venue's native unit. A blank fee stays unknown (never a default); a typed fee
 * is the viewer's own entry ("assumption"). An entry venue takes no price: its pick is priced by the entry.
 */
export function typedQuote(provider: ProviderInfo, market: Market, side: Side, input: PriceInput, nowIso: string, id?: string): TypedQuote {
  const base = { id: id ?? localId("typed"), marketId: market.id, provider: provider.id, side, provenance: "manual" as const, observedAt: nowIso, expiresAt: null };
  if (provider.kind === "entry") return { ok: true, quote: { ...base, price: { kind: "entry-pick" } } };
  const feeText = input.fee.trim();
  let fee: Fee = { status: "unknown" };
  if (feeText !== "") {
    const f = Number(feeText);
    if (!Number.isFinite(f) || f < 0 || f > 100) return { ok: false, text: "A fee is 0 to 100 cents per $1 of payout, or leave it blank for unknown." };
    fee = { status: "known", centsPerDollarPayout: f, basis: "assumption" };
  }
  const v = Number(input.price.trim().replace(/^\+/, "").replace("−", "-"));
  if (input.price.trim() === "" || !Number.isFinite(v)) return { ok: false, text: provider.kind === "exchange" ? "Type the ask in cents, 0.01 to 100." : "Type American odds, like -110 or +145." };
  if (provider.kind === "exchange") {
    if (v < 0.01 || v > 100) return { ok: false, text: "The ask is 0.01 to 100 cents." };
    return { ok: true, quote: { ...base, price: { kind: "binary", askCents: v, fee } } };
  }
  if (Math.abs(v) < 100 || Math.abs(v) > 100000) return { ok: false, text: "American odds are -100 or lower, or +100 or higher." };
  return { ok: true, quote: { ...base, price: { kind: "sportsbook", americanOdds: Math.round(v), fee } } };
}

/** A stored quote with the fee the viewer typed (labelled their entry); the stored price itself is unchanged. */
export function withTypedFee(quote: Quote, feeText: string): TypedQuote {
  if (quote.price.kind === "entry-pick") return { ok: true, quote };
  const t = feeText.trim();
  if (t === "") return { ok: true, quote };
  const f = Number(t);
  if (!Number.isFinite(f) || f < 0 || f > 100) return { ok: false, text: "A fee is 0 to 100 cents per $1 of payout." };
  return { ok: true, quote: { ...quote, price: { ...quote.price, fee: { status: "known", centsPerDollarPayout: f, basis: "assumption" } } } };
}

// ------------------------------------------------------------------------------------------------ venue cards

export interface VenueCard {
  readonly provider: ProviderInfo;
  /** The stored snapshot for this venue, market and side, or null ("unavailable"). */
  readonly stored: Quote | null;
  /** The price the viewer typed for this slot, if any (kept in the workspace). */
  readonly typed: Quote | null;
  /** The quote "add to slip" would use: the typed one when present, else the stored one. */
  readonly active: Quote | null;
  readonly cost: NormalisedCost | null;
  readonly freshness: Freshness | null;
  /** Planned legs in the slip on this venue, market and side: "price when added". */
  readonly inSlip: readonly SlipLeg[];
}

/** One card per venue: venues with a price first, then the unavailable ones, each group in name sequence (never by price). */
export function venueCards(input: { providers: readonly ProviderInfo[]; stored: Catalog | null; workspace: SlipWorkspace; market: Market; side: Side; now: number }): VenueCard[] {
  const cards = byName(input.providers).map((provider) => {
    const slot = input.stored ? quoteSlot(input.stored, input.market.id, provider.id, input.side) : null;
    const stored = slot?.state === "available" ? slot.quote : null;
    const typed = input.workspace.manualQuotes.find((q) => q.marketId === input.market.id && q.provider === provider.id && q.side === input.side) ?? null;
    const active = typed ?? stored;
    return {
      provider,
      stored,
      typed,
      active,
      cost: active ? normalisedCost(active) : null,
      freshness: active ? freshness(active, input.now) : null,
      inSlip: input.workspace.draft.legs.filter((l) => l.role === "planned" && l.market.id === input.market.id && l.quote.side === input.side && l.quote.provider === provider.id),
    };
  });
  return [...cards.filter((c) => c.active !== null), ...cards.filter((c) => c.active === null)];
}

export type Crown = { shown: true; provider: string } | { shown: false; reasons: readonly string[] };

/**
 * A "lowest cost per $1 of payout" mark, only when every pair of priced cards is a valid like-for-like comparison by the
 * catalog's rules (same market, verified settlement, known fees, current prices). Otherwise no mark, with the reasons.
 */
export function lowestCostCrown(cards: readonly VenueCard[], market: Market, now: number): Crown {
  const priced = cards.filter((c) => c.active !== null && c.cost?.ok === true);
  if (priced.length < 2) return { shown: false, reasons: ["Fewer than two venues have a usable cost for this side."] };
  const reasons = new Set<string>();
  for (let i = 0; i < priced.length; i++) {
    for (let j = i + 1; j < priced.length; j++) {
      const a = priced[i]?.active;
      const b = priced[j]?.active;
      if (!a || !b) continue;
      for (const r of comparability({ market, quote: a }, { market, quote: b }, now).reasons) reasons.add(r);
    }
  }
  if (reasons.size) return { shown: false, reasons: [...reasons] };
  let best: VenueCard | null = null;
  for (const c of priced) if (c.cost?.ok && (best === null || (best.cost?.ok && c.cost.cents < best.cost.cents))) best = c;
  return best ? { shown: true, provider: best.provider.id } : { shown: false, reasons: [] };
}

// ------------------------------------------------------------------------------------------------ before / after

/** The slip as it is and the slip with one more planned leg: the before/after the screens show. */
export function previewWithLeg(draft: SlipDraft, quote: Quote, market: Market, amount: number | null | undefined, now: number): { before: SlipEvaluation; after: SlipEvaluation | null; text: string | null } {
  const before = evaluateDraft(draft, { now });
  const r = addQuote(draft, quote, market, amount === undefined ? {} : { amount });
  if (!r.ok) return { before, after: null, text: r.text };
  return { before, after: evaluateDraft(r.draft, { now }), text: null };
}

// ------------------------------------------------------------------------------------------------ hedge finder

/** Recorded positions (already placed elsewhere), in the slip's own sequence. */
export function heldPositions(draft: SlipDraft): SlipLeg[] {
  return draft.legs.filter((l) => l.role === "existing");
}

/** The side a hedge takes against this held leg: the opposite one. */
export function hedgeSideFor(leg: SlipLeg): Side {
  return oppositeSide(leg.quote.side);
}

export interface HedgeCard extends VenueCard {
  /** The sized hedge leg (amount covers the uncovered gross payout), or why it is not sized. */
  readonly hedge: { ok: true; leg: SlipLeg; sized: boolean; text: string } | { ok: false; text: string } | null;
}

/** Venue cards for the opposite side of a held market, each with its hedge sized by hedgeMath through slipModel. */
export function hedgeCards(input: { providers: readonly ProviderInfo[]; stored: Catalog | null; workspace: SlipWorkspace; market: Market; side: Side; now: number }): HedgeCard[] {
  return venueCards(input).map((c) => {
    if (!c.active) return { ...c, hedge: null };
    const r = makeHedgeLeg(input.workspace.draft, c.active, input.market);
    return { ...c, hedge: r.ok ? { ok: true, leg: r.leg, sized: r.sized, text: r.text } : { ok: false, text: r.text } };
  });
}
