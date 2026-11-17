// Shared types for the betting tools (pure logic, no screens). Everything here is arithmetic on PRICES a viewer typed or
// the export supplied: no probability, edge, correlation or synergy is computed or stored by this module. The nba/ model
// is the only source of those. Money: cents per $1 of gross payout for normalised cost; dollars elsewhere.
import type { Stat } from "../types";

/** A provider is an open set: kalshi, draftkings, prizepicks and any book id the export carries. */
export type ProviderId = string;
/** How a provider prices: a binary contract (cents per $1), a sportsbook stake (American odds), or an entry (one payout). */
export type ProviderKind = "exchange" | "sportsbook" | "entry";

export interface ProviderInfo {
  readonly id: ProviderId;
  readonly label: string;
  readonly kind: ProviderKind;
}

export type Side = "yes" | "no";

/** Where a quote came from. A manual quote is what the viewer typed; sample is the labelled demo only. */
export type Provenance = "live" | "delayed" | "manual" | "sample";

/** Where a market or draft record came from. */
export type RecordOrigin = "export" | "manual" | "sample";

/** The YES side of every market here is "the stat finishes above `line`" (an over); NO is the complement. */
export interface MarketIdentity {
  /** The game id the export uses. */
  readonly eventId: string;
  /** The game's date, YYYY-MM-DD (US Eastern night). */
  readonly eventDate: string;
  readonly playerId: string;
  readonly stat: Stat;
  readonly line: number;
  /** "full_game" unless a provider settles on a part of the game. */
  readonly period: string;
  /**
   * The verified rule set under which every provider's quote on this market settles identically (DNP, void, overtime).
   * Null means unverified: cross-provider coverage on this market is then never shown.
   */
  readonly settlementRuleId: string | null;
}

export interface Market extends MarketIdentity {
  readonly id: string;
  /** Display text only. */
  readonly playerName: string;
  /** Display text only, e.g. "ORL @ PHX". */
  readonly eventLabel: string;
  readonly origin: RecordOrigin;
}

/** A fee is either known (with its basis) or unknown. Unknown is never replaced by a default number. */
export type Fee =
  | { readonly status: "known"; readonly centsPerDollarPayout: number; readonly basis: "provider" | "assumption" }
  | { readonly status: "unknown" };

/** The native price of one quote. An entry pick has no price of its own: the entry's payout rule prices the group. */
export type QuotePrice =
  | { readonly kind: "binary"; readonly askCents: number; readonly fee: Fee }
  | { readonly kind: "sportsbook"; readonly americanOdds: number; readonly fee: Fee }
  | { readonly kind: "entry-pick" };

export interface Quote {
  readonly id: string;
  readonly marketId: string;
  readonly provider: ProviderId;
  readonly side: Side;
  readonly price: QuotePrice;
  readonly provenance: Provenance;
  /** When the price was seen (ISO instant with zone). */
  readonly observedAt: string;
  /** When the source says the price stops holding; null when it does not say. */
  readonly expiresAt: string | null;
}

/** A slot on the board: one provider's price for one side of one market, or a plain statement that there is none. */
export type QuoteSlot =
  | { readonly state: "available"; readonly quote: Quote }
  | { readonly state: "unavailable"; readonly provider: ProviderId; readonly marketId: string; readonly side: Side };

export type Freshness = "current" | "stale" | "unknown" | "sample";

/** The injected market catalog: providers, markets and the current quote board. */
export interface Catalog {
  readonly providers: readonly ProviderInfo[];
  readonly markets: readonly Market[];
  readonly quotes: readonly Quote[];
}

export type LegRole = "planned" | "existing";

/**
 * One leg of the rolling slip. `quote` and `market` are frozen snapshots taken when the leg was made: a later board
 * refresh never moves them. `amount` is native: whole contracts (exchange) or dollars of stake (sportsbook); an entry pick
 * has no amount of its own (null), the entry's stake is on the draft.
 */
export interface SlipLeg {
  readonly id: string;
  readonly quote: Quote;
  readonly market: Market;
  readonly amount: number | null;
  readonly enabled: boolean;
  readonly role: LegRole;
  /** When the viewer recorded it as already placed elsewhere (local record only); null while planned. */
  readonly placedAt: string | null;
}

/** One entry: one stake, one all-correct gross multiplier (stake included). No flex tiers, pushes or voids. */
export interface EntryTerms {
  readonly stake: number;
  readonly multiplier: number;
}

export interface SlipDraft {
  readonly legs: readonly SlipLeg[];
  readonly entry: EntryTerms;
  readonly title: string;
  readonly thesis: string;
  readonly tags: readonly string[];
  readonly origin: "user" | "sample";
}

export type IdeaStatus = "watching" | "paper" | "settled";

/** A saved idea: an independent, frozen snapshot of a draft plus the viewer's own recorded outcomes. */
export interface SavedIdea extends SlipDraft {
  readonly id: string;
  readonly createdAt: string;
  readonly status: IdeaStatus;
  /** Manual paper outcomes by market id; never a provider-verified settlement. */
  readonly outcomes: Readonly<Record<string, Side>>;
}

/**
 * A pick added from a game or player page before any price is chosen: the market and side exactly as the page showed
 * them. It carries no price and no amount; it becomes a slip leg only when the viewer picks a venue price (Compare) or
 * types one. It is never counted in cost, outcomes or coverage.
 */
export interface SlipPick {
  readonly id: string;
  readonly market: Market;
  readonly side: Side;
  /** Which page added it ("leg", "game", "compare", "custom"). */
  readonly from: string;
  readonly addedAt: string;
}

/** What the browser keeps per scope: the rolling slip, saved ideas, quotes the viewer typed, and picks awaiting a price. */
export interface SlipWorkspace {
  readonly draft: SlipDraft;
  readonly ideas: readonly SavedIdea[];
  readonly manualQuotes: readonly Quote[];
  /** Absent in documents written before picks existed; read as an empty list. */
  readonly picks: readonly SlipPick[];
}

/** The app's error style: a stable snake_case code plus one line of plain text. */
export interface Failure<C extends string = string> {
  readonly ok: false;
  readonly code: C;
  readonly text: string;
}
export type Result<T, C extends string = string> = ({ readonly ok: true } & T) | Failure<C>;
