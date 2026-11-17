// The labelled demo: four invented markets and their invented prices, kept from the donor kit so its worked examples
// (53c vs 54.954955c; $10 at +145 = $24.50) still hold. Nothing here runs on load or on an error: each function runs only
// when a screen calls it after an explicit "load the sample" action, and every record it makes says origin/provenance
// "sample". Dates are before season 2025 and the event ids say "sample"; no price here is a real quote.
import { buildCatalog, KNOWN_PROVIDERS } from "./catalog";
import { emptyDraft, makeLeg } from "./slipModel";
import type { Catalog, Market, Quote, Side, SlipDraft, SlipWorkspace } from "./types";

export const SAMPLE_STAMP = "2024-11-05T02:00:00.000Z";
const SAMPLE_DATE = "2024-11-04";

const MARKET_ROWS: readonly (readonly [string, string, string, Market["stat"], number])[] = [
  ["collin", "Collin Gillespie", "sample-event-orl-phx", "fg3m", 2.5],
  ["bobby", "Bobby Portis", "sample-event-mia-gsw", "reb", 5.5],
  ["oso", "Oso Ighodaro", "sample-event-orl-phx", "reb", 3.5],
  ["jalen", "Jalen Green", "sample-event-orl-phx", "ast", 3.5],
];

/** Donor prices [yes, no]: exchange ask (cents), exchange fee per contract (cents), sportsbook American odds. */
const PRICES: Record<string, { ask: [number, number]; fee: [number, number]; odds: [number, number] }> = {
  collin: { ask: [37, 52], fee: [2, 1], odds: [150, -122] },
  bobby: { ask: [45, 56], fee: [2, 2], odds: [105, -125] },
  oso: { ask: [27, 75], fee: [2, 2], odds: [235, -290] },
  jalen: { ask: [39, 63], fee: [2, 2], odds: [145, -175] },
};

/** The sample market id for a donor short name ("jalen" -> "sample-jalen"). */
export const sampleMarketId = (short: string): string => `sample-${short}`;

function sampleMarkets(): Market[] {
  return MARKET_ROWS.map(([short, name, eventId, stat, line]) => ({
    id: sampleMarketId(short),
    eventId,
    eventDate: SAMPLE_DATE,
    playerId: `sample-player-${short}`,
    stat,
    line,
    period: "full_game",
    settlementRuleId: null,
    playerName: name,
    eventLabel: eventId === "sample-event-mia-gsw" ? "Sample game · MIA @ GSW" : "Sample game · ORL @ PHX",
    origin: "sample",
  }));
}

function sampleQuotes(): Quote[] {
  const out: Quote[] = [];
  for (const [short] of MARKET_ROWS) {
    const p = PRICES[short];
    if (p === undefined) continue;
    (["yes", "no"] as Side[]).forEach((side, i) => {
      const base = { marketId: sampleMarketId(short), side, provenance: "sample" as const, observedAt: SAMPLE_STAMP, expiresAt: null };
      out.push({ ...base, id: `${short}-kalshi-${side}`, provider: "kalshi", price: { kind: "binary", askCents: p.ask[i] ?? 0, fee: { status: "known", centsPerDollarPayout: p.fee[i] ?? 0, basis: "assumption" } } });
      out.push({ ...base, id: `${short}-draftkings-${side}`, provider: "draftkings", price: { kind: "sportsbook", americanOdds: p.odds[i] ?? 0, fee: { status: "known", centsPerDollarPayout: 0, basis: "assumption" } } });
      out.push({ ...base, id: `${short}-prizepicks-${side}`, provider: "prizepicks", price: { kind: "entry-pick" } });
    });
  }
  return out;
}

/** The labelled sample catalog (four markets, three providers, both sides). Call only on an explicit action. */
export function sampleCatalog(): Catalog {
  return buildCatalog({ providers: KNOWN_PROVIDERS, markets: sampleMarkets(), quotes: sampleQuotes() }).catalog;
}

/** A sample quote and its market, by donor short name, provider and side. Throws if the sample lacks it (a bug). */
export function sampleQuote(short: string, provider: string, side: Side): { quote: Quote; market: Market } {
  const cat = sampleCatalog();
  const quote = cat.quotes.find((q) => q.marketId === sampleMarketId(short) && q.provider === provider && q.side === side);
  const market = cat.markets.find((m) => m.id === sampleMarketId(short));
  if (!quote || !market) throw new Error(`sample has no ${short}/${provider}/${side}`);
  return { quote, market };
}

/** The donor's first-run NBA example: one planned DraftKings YES leg on Jalen Green, $10 at +145 ($24.50 gross). */
export function buildNBASampleDraft(): SlipDraft {
  const { quote, market } = sampleQuote("jalen", "draftkings", "yes");
  return {
    ...emptyDraft(),
    origin: "sample",
    title: "NBA sample · build your idea",
    thesis: "Sample NBA example: a $10 Jalen Green YES position at +145. Explore adding players and recording positions; this is not a live quote, a placed bet, or a recommendation.",
    legs: [makeLeg(quote, market, "planned", { amount: 10 })],
  };
}

/** The donor's hedge example: a recorded $40 DraftKings YES on Collin at +150 and 100 planned Kalshi NO at 52c + 1c. */
export function buildDemoDraft(): SlipDraft {
  const yes = sampleQuote("collin", "draftkings", "yes");
  const no = sampleQuote("collin", "kalshi", "no");
  return {
    ...emptyDraft(),
    origin: "sample",
    title: "Collin · compare the other side",
    thesis: "Explore a Kalshi NO position against a sample DraftKings YES position. Settlement equivalence is unverified.",
    tags: ["cross-provider", "complementary"],
    legs: [makeLeg(yes.quote, yes.market, "existing", { amount: 40, placedAt: SAMPLE_STAMP }), makeLeg(no.quote, no.market, "planned", { amount: 100 })],
  };
}

/**
 * The workspace a screen shows after "load the sample": the NBA sample draft and nothing else. It replaces nothing by
 * itself; the caller decides (with the viewer's confirmation) whether to stage it over their own draft.
 */
export function sampleWorkspace(): SlipWorkspace {
  return { draft: buildNBASampleDraft(), ideas: [], manualQuotes: [], picks: [] };
}
