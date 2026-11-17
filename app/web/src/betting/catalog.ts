// The injected market catalog: providers, markets and the quote board, validated record by record. A bad record is
// reported (which one, and why) and dropped; the good ones are kept. A missing provider or side is "unavailable", a state
// and not an error. Nothing here reads the network or a model, and nothing invents a price to fill a gap.
import { isIsoDate, instantMs } from "../time";
import { STATS, type Stat } from "../types";
import { frozenCopy } from "./ids";
import type { Catalog, Fee, Freshness, Market, ProviderId, ProviderInfo, ProviderKind, Quote, QuotePrice, QuoteSlot, Side } from "./types";

export const KNOWN_PROVIDERS: readonly ProviderInfo[] = Object.freeze([
  Object.freeze({ id: "kalshi", label: "Kalshi", kind: "exchange" as const }),
  Object.freeze({ id: "draftkings", label: "DraftKings", kind: "sportsbook" as const }),
  Object.freeze({ id: "prizepicks", label: "PrizePicks", kind: "entry" as const }),
]);

/** A planned price older than this is stale (and an earlier `expiresAt` wins). A parameter everywhere it is used. */
export const DEFAULT_MAX_QUOTE_AGE_MS = 15 * 60 * 1000;

/** Season 2025 (2025-26) is the frozen holdout: no record dated inside it is accepted. */
export const HOLDOUT_FIRST_DATE = "2025-07-01";
export const HOLDOUT_LAST_DATE = "2026-06-30";

const ID_MAX = 160;
const PROVIDER_ID = /^[a-z0-9][a-z0-9_.-]{0,39}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
export const isId = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0 && v.length <= ID_MAX;
export const isText = (v: unknown, max: number): v is string => typeof v === "string" && v.length <= max;
export const isFiniteIn = (v: unknown, lo: number, hi: number): v is number => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
/** An ISO instant that names its zone (Z or an offset); a naive time is refused rather than guessed. */
export const isInstant = (v: unknown): v is string =>
  typeof v === "string" && v.length < 80 && /(Z|[+-]\d{2}:?\d{2})$/.test(v) && Number.isFinite(instantMs(v));
export const isStat = (v: unknown): v is Stat => typeof v === "string" && (STATS as readonly string[]).includes(v);
export const isSide = (v: unknown): v is Side => v === "yes" || v === "no";

function isCalendarDate(v: unknown): v is string {
  return typeof v === "string" && DATE.test(v) && isIsoDate(v);
}

export function inHoldoutSeason(date: string): boolean {
  return date >= HOLDOUT_FIRST_DATE && date <= HOLDOUT_LAST_DATE;
}

/** A provider registry for these book ids: known providers keep their kind, any other id is a sportsbook. */
export function providersFor(ids: readonly string[]): ProviderInfo[] {
  const out: ProviderInfo[] = [...KNOWN_PROVIDERS];
  for (const raw of ids) {
    const id = raw.trim().toLowerCase();
    if (!PROVIDER_ID.test(id) || out.some((p) => p.id === id)) continue;
    out.push(Object.freeze({ id, label: id, kind: "sportsbook" }));
  }
  return out;
}

export function providerKind(providers: readonly ProviderInfo[], id: ProviderId): ProviderKind | null {
  return providers.find((p) => p.id === id)?.kind ?? null;
}

const KIND_FOR_PRICE: Record<QuotePrice["kind"], ProviderKind> = { binary: "exchange", sportsbook: "sportsbook", "entry-pick": "entry" };

// ------------------------------------------------------------------------------------------------ per-record checks

export type CheckResult<T> = { ok: true; value: T } | { ok: false; reason: string };

export function checkProvider(v: unknown): CheckResult<ProviderInfo> {
  if (!isObject(v)) return { ok: false, reason: "not an object" };
  if (typeof v["id"] !== "string" || !PROVIDER_ID.test(v["id"])) return { ok: false, reason: "id must be lower-case letters, digits, '_', '.', '-'" };
  if (!isText(v["label"], 60) || v["label"].trim() === "") return { ok: false, reason: "label missing" };
  const kind = v["kind"];
  if (kind !== "exchange" && kind !== "sportsbook" && kind !== "entry") return { ok: false, reason: "kind must be exchange, sportsbook or entry" };
  return { ok: true, value: { id: v["id"], label: v["label"], kind } };
}

export function checkMarket(v: unknown): CheckResult<Market> {
  if (!isObject(v)) return { ok: false, reason: "not an object" };
  for (const k of ["id", "eventId", "playerId", "period"] as const) if (!isId(v[k])) return { ok: false, reason: `${k} missing` };
  if (!isCalendarDate(v["eventDate"])) return { ok: false, reason: "eventDate must be a real YYYY-MM-DD date" };
  if (inHoldoutSeason(v["eventDate"])) return { ok: false, reason: "eventDate is in season 2025, the frozen holdout" };
  if (!isStat(v["stat"])) return { ok: false, reason: `stat must be one of ${STATS.join(", ")}` };
  if (!isFiniteIn(v["line"], 0, 1000)) return { ok: false, reason: "line must be a number from 0 to 1000" };
  const rule = v["settlementRuleId"];
  if (rule !== null && !isId(rule)) return { ok: false, reason: "settlementRuleId must be an id or null (unverified)" };
  if (!isText(v["playerName"], 100) || v["playerName"].trim() === "") return { ok: false, reason: "playerName missing" };
  if (!isText(v["eventLabel"], 120)) return { ok: false, reason: "eventLabel must be text" };
  const origin = v["origin"];
  if (origin !== "export" && origin !== "manual" && origin !== "sample") return { ok: false, reason: "origin must be export, manual or sample" };
  return {
    ok: true,
    value: {
      id: v["id"] as string,
      eventId: v["eventId"] as string,
      eventDate: v["eventDate"],
      playerId: v["playerId"] as string,
      stat: v["stat"],
      line: v["line"],
      period: v["period"] as string,
      settlementRuleId: rule,
      playerName: v["playerName"],
      eventLabel: v["eventLabel"],
      origin,
    },
  };
}

function checkFee(v: unknown): CheckResult<Fee> {
  if (!isObject(v)) return { ok: false, reason: "fee missing (use {status: 'unknown'} when it is not known)" };
  if (v["status"] === "unknown") return { ok: true, value: { status: "unknown" } };
  if (v["status"] !== "known") return { ok: false, reason: "fee.status must be known or unknown" };
  if (!isFiniteIn(v["centsPerDollarPayout"], 0, 100)) return { ok: false, reason: "fee must be 0 to 100 cents per $1 payout" };
  const basis = v["basis"];
  if (basis !== "provider" && basis !== "assumption") return { ok: false, reason: "fee.basis must be provider or assumption" };
  return { ok: true, value: { status: "known", centsPerDollarPayout: v["centsPerDollarPayout"], basis } };
}

export function checkPrice(v: unknown): CheckResult<QuotePrice> {
  if (!isObject(v)) return { ok: false, reason: "price missing" };
  if (v["kind"] === "entry-pick") {
    const extra = Object.keys(v).filter((k) => k !== "kind");
    return extra.length ? { ok: false, reason: "an entry pick carries no price of its own" } : { ok: true, value: { kind: "entry-pick" } };
  }
  const fee = checkFee(v["fee"]);
  if (!fee.ok) return fee;
  if (v["kind"] === "binary") {
    if (!isFiniteIn(v["askCents"], 0.01, 100)) return { ok: false, reason: "askCents must be 0.01 to 100" };
    return { ok: true, value: { kind: "binary", askCents: v["askCents"], fee: fee.value } };
  }
  if (v["kind"] === "sportsbook") {
    const o = v["americanOdds"];
    if (!isFiniteIn(o, -100000, 100000) || Math.abs(o) < 100) return { ok: false, reason: "americanOdds must be at most -100 or at least +100" };
    return { ok: true, value: { kind: "sportsbook", americanOdds: o, fee: fee.value } };
  }
  return { ok: false, reason: "price.kind must be binary, sportsbook or entry-pick" };
}

/** One quote on its own terms. With a provider registry, the price kind must also match the provider's kind. */
export function checkQuote(v: unknown, providers?: readonly ProviderInfo[]): CheckResult<Quote> {
  if (!isObject(v)) return { ok: false, reason: "not an object" };
  if (!isId(v["id"])) return { ok: false, reason: "id missing" };
  if (!isId(v["marketId"])) return { ok: false, reason: "marketId missing" };
  if (typeof v["provider"] !== "string" || !PROVIDER_ID.test(v["provider"])) return { ok: false, reason: "provider id invalid" };
  if (!isSide(v["side"])) return { ok: false, reason: "side must be yes or no" };
  const prov = v["provenance"];
  if (prov !== "live" && prov !== "delayed" && prov !== "manual" && prov !== "sample") return { ok: false, reason: "provenance must be live, delayed, manual or sample" };
  if (!isInstant(v["observedAt"])) return { ok: false, reason: "observedAt must be an ISO instant with a zone" };
  const exp = v["expiresAt"];
  if (exp !== null && !isInstant(exp)) return { ok: false, reason: "expiresAt must be an ISO instant or null" };
  const price = checkPrice(v["price"]);
  if (!price.ok) return price;
  if (providers !== undefined) {
    const kind = providerKind(providers, v["provider"]);
    if (kind === null) return { ok: false, reason: `provider ${v["provider"]} is not in the registry` };
    if (KIND_FOR_PRICE[price.value.kind] !== kind) return { ok: false, reason: `a ${kind} provider cannot carry a ${price.value.kind} price` };
  }
  return {
    ok: true,
    value: { id: v["id"], marketId: v["marketId"], provider: v["provider"], side: v["side"], price: price.value, provenance: prov, observedAt: v["observedAt"], expiresAt: exp },
  };
}

// ------------------------------------------------------------------------------------------------ catalog

export interface RejectedRecord {
  readonly kind: "provider" | "market" | "quote";
  readonly index: number;
  /** The record's id when it had a readable one. */
  readonly id: string | null;
  readonly reason: string;
}

export interface CatalogBuild {
  readonly catalog: Catalog;
  readonly rejected: readonly RejectedRecord[];
}

/** The identity of a market, apart from its id: two records with the same identity are the same market. */
export function marketIdentityKey(m: Market): string {
  return [m.eventId, m.eventDate, m.playerId, m.stat, String(m.line), m.period].join("|");
}

/** Quotes are unique within event / provider / market / side. */
export function quoteSlotKey(eventId: string, provider: ProviderId, marketId: string, side: Side): string {
  return [eventId, provider, marketId, side].join("|");
}

const idOf = (v: unknown): string | null => (isObject(v) && isId(v["id"]) ? v["id"] : null);

/**
 * Builds a frozen catalog from untrusted records. Each bad or duplicate record is listed in `rejected` with its reason;
 * every valid record is kept. There is no required count of markets, providers or quotes.
 */
export function buildCatalog(input: { providers?: readonly unknown[]; markets: readonly unknown[]; quotes: readonly unknown[] }): CatalogBuild {
  const rejected: RejectedRecord[] = [];
  const providers: ProviderInfo[] = [];
  for (const [index, raw] of (input.providers ?? KNOWN_PROVIDERS).entries()) {
    const r = checkProvider(raw);
    if (!r.ok) rejected.push({ kind: "provider", index, id: idOf(raw), reason: r.reason });
    else if (providers.some((p) => p.id === r.value.id)) rejected.push({ kind: "provider", index, id: r.value.id, reason: "duplicate provider id" });
    else providers.push(r.value);
  }
  const markets: Market[] = [];
  const identities = new Set<string>();
  for (const [index, raw] of input.markets.entries()) {
    const r = checkMarket(raw);
    if (!r.ok) {
      rejected.push({ kind: "market", index, id: idOf(raw), reason: r.reason });
      continue;
    }
    const key = marketIdentityKey(r.value);
    if (markets.some((m) => m.id === r.value.id)) rejected.push({ kind: "market", index, id: r.value.id, reason: "duplicate market id" });
    else if (identities.has(key)) rejected.push({ kind: "market", index, id: r.value.id, reason: "same event, player, stat, line and period as an earlier market" });
    else {
      identities.add(key);
      markets.push(r.value);
    }
  }
  const byId = new Map(markets.map((m) => [m.id, m]));
  const quotes: Quote[] = [];
  const slots = new Set<string>();
  for (const [index, raw] of input.quotes.entries()) {
    const r = checkQuote(raw, providers);
    if (!r.ok) {
      rejected.push({ kind: "quote", index, id: idOf(raw), reason: r.reason });
      continue;
    }
    const market = byId.get(r.value.marketId);
    if (market === undefined) {
      rejected.push({ kind: "quote", index, id: r.value.id, reason: `market ${r.value.marketId} is not in the catalog` });
      continue;
    }
    const slot = quoteSlotKey(market.eventId, r.value.provider, market.id, r.value.side);
    if (quotes.some((q) => q.id === r.value.id)) rejected.push({ kind: "quote", index, id: r.value.id, reason: "duplicate quote id" });
    else if (slots.has(slot)) rejected.push({ kind: "quote", index, id: r.value.id, reason: "a quote for this event, provider, market and side is already on the board" });
    else {
      slots.add(slot);
      quotes.push(r.value);
    }
  }
  return { catalog: frozenCopy({ providers, markets, quotes }), rejected };
}

/**
 * A quote-board refresh: a new frozen catalog with the new quotes, same providers and markets. Saved legs and ideas hold
 * their own frozen snapshots, so nothing already captured moves.
 */
export function refreshQuotes(catalog: Catalog, quotes: readonly unknown[]): CatalogBuild {
  return buildCatalog({ providers: catalog.providers, markets: catalog.markets, quotes });
}

export function findMarket(catalog: Catalog, marketId: string): Market | null {
  return catalog.markets.find((m) => m.id === marketId) ?? null;
}

/** One provider's price for one side of one market, or "unavailable" (never a filled-in value). */
export function quoteSlot(catalog: Catalog, marketId: string, provider: ProviderId, side: Side): QuoteSlot {
  const q = catalog.quotes.find((x) => x.marketId === marketId && x.provider === provider && x.side === side);
  return q ? { state: "available", quote: q } : { state: "unavailable", provider, marketId, side };
}

/** The board for one market and side: one slot per registered provider, as listed in the registry. */
export function boardFor(catalog: Catalog, marketId: string, side: Side): QuoteSlot[] {
  return catalog.providers.map((p) => quoteSlot(catalog, marketId, p.id, side));
}

export function oppositeSide(side: Side): Side {
  return side === "yes" ? "no" : "yes";
}

// ------------------------------------------------------------------------------------------------ freshness

/**
 * Is a quote's price still current? Sample is its own state. Live, delayed and manual prices are stale after `maxAgeMs`
 * or at `expiresAt`, whichever comes first; an unreadable or future time is unknown. A typed price is not live because
 * its timestamp is recent: provenance stays "manual" either way.
 */
export function freshness(q: Pick<Quote, "provenance" | "observedAt" | "expiresAt">, now: number, maxAgeMs = DEFAULT_MAX_QUOTE_AGE_MS): Freshness {
  if (q.provenance === "sample") return "sample";
  const seen = instantMs(q.observedAt);
  if (!Number.isFinite(seen) || !Number.isFinite(now) || seen > now) return "unknown";
  if (q.expiresAt !== null) {
    const exp = instantMs(q.expiresAt);
    if (!Number.isFinite(exp) || exp < seen) return "unknown";
    if (now >= exp) return "stale";
  }
  return now - seen > maxAgeMs ? "stale" : "current";
}

// ------------------------------------------------------------------------------------------------ comparability

export interface Comparability {
  readonly comparable: boolean;
  readonly reasons: readonly string[];
}

/**
 * Necessary conditions for ranking two quotes by cost for the same $1 payout. `comparable: true` says only that the two
 * describe the same outcome on verified terms with current known prices; it is not a claim that money can be made.
 */
export function comparability(a: { market: Market; quote: Quote }, b: { market: Market; quote: Quote }, now: number, maxAgeMs = DEFAULT_MAX_QUOTE_AGE_MS): Comparability {
  const reasons: string[] = [];
  if (a.quote.marketId !== a.market.id || b.quote.marketId !== b.market.id) reasons.push("Quote and market do not match.");
  for (const k of ["eventId", "eventDate", "playerId", "stat", "period"] as const) if (a.market[k] !== b.market[k]) reasons.push(`Different ${k}.`);
  if (a.market.line !== b.market.line) reasons.push("Different line.");
  if (a.market.settlementRuleId === null || b.market.settlementRuleId === null || a.market.settlementRuleId !== b.market.settlementRuleId) {
    reasons.push("Settlement rules (DNP, void, overtime) are unverified or differ.");
  }
  if (a.quote.side !== b.quote.side) reasons.push("Different sides; ranking cost needs the same side.");
  for (const q of [a.quote, b.quote]) {
    if (q.price.kind === "entry-pick") reasons.push(`${q.provider} prices whole entries; a single pick has no price.`);
    else if (q.price.fee.status !== "known") reasons.push(`${q.provider} fee is unknown.`);
    const f = freshness(q, now, maxAgeMs);
    if (f !== "current") reasons.push(`${q.provider} price is ${f === "sample" ? "a labelled sample" : f}.`);
  }
  return { comparable: reasons.length === 0, reasons: [...new Set(reasons)] };
}
