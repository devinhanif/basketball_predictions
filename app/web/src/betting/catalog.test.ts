// Adapted from the donor kit's tests/integration/feed.test.ts (validation and comparison cases), plus per-record catalog
// validation, partial availability and the holdout refusal.
import { describe, expect, it } from "vitest";
import { boardFor, buildCatalog, comparability, freshness, KNOWN_PROVIDERS, oppositeSide, providersFor, quoteSlot } from "./catalog";
import { createSlipWorkspace, emptyDraft, evaluateDraft, makeLeg, validateSlipWorkspace } from "./slipModel";
import type { Market, Quote } from "./types";

const NOW_ISO = "2026-11-17T02:00:30.000Z";
const now = Date.parse(NOW_ISO);
const market = (over: Partial<Market> = {}): Market => ({
  id: "market-from-export",
  eventId: "event-from-export",
  eventDate: "2026-11-16",
  playerId: "player-from-export",
  stat: "reb",
  line: 5.5,
  period: "full_game",
  settlementRuleId: "rules:v1",
  playerName: "Example Player",
  eventLabel: "AAA @ BBB",
  origin: "export",
  ...over,
});
const quote = (over: Partial<Quote> = {}): Quote => ({
  id: "quote-1",
  marketId: "market-from-export",
  provider: "kalshi",
  side: "yes",
  price: { kind: "binary", askCents: 45, fee: { status: "known", centsPerDollarPayout: 1, basis: "provider" } },
  provenance: "live",
  observedAt: "2026-11-17T02:00:00.000Z",
  expiresAt: "2026-11-17T02:01:00.000Z",
  ...over,
});

describe("donor feed cases, adapted", () => {
  it("a neutral example with an unknown fee and unknown rules is accepted as-is, with nothing filled in", () => {
    const b = buildCatalog({
      markets: [market({ settlementRuleId: null })],
      quotes: [quote({ provenance: "sample", expiresAt: null, price: { kind: "binary", askCents: 45, fee: { status: "unknown" } } })],
    });
    expect(b.rejected).toEqual([]);
    expect(b.catalog.quotes[0]?.price).toEqual({ kind: "binary", askCents: 45, fee: { status: "unknown" } });
    expect(b.catalog.markets[0]?.settlementRuleId).toBeNull();
    expect(freshness(b.catalog.quotes[0] as Quote, now)).toBe("sample");
    expect(JSON.stringify(b.catalog)).not.toMatch(/probab|edge|correl|synerg/i);
  });

  it("a quote whose market is missing is rejected by itself; the rest is kept", () => {
    const b = buildCatalog({ markets: [market()], quotes: [quote(), quote({ id: "orphan", marketId: "missing" })] });
    expect(b.catalog.quotes.map((q) => q.id)).toEqual(["quote-1"]);
    expect(b.rejected).toEqual([{ kind: "quote", index: 1, id: "orphan", reason: "market missing is not in the catalog" }]);
  });

  it("matching known current live contracts qualify for a cost comparison only", () => {
    const m = market();
    const q = quote();
    expect(comparability({ market: m, quote: q }, { market: m, quote: { ...q, id: "q2", provider: "draftkings" } }, now)).toEqual({ comparable: true, reasons: [] });
  });

  it("a different event, date, period, line or settlement rule, or an unverified rule, blocks comparison", () => {
    const m = market();
    const q = quote();
    for (const change of [{ eventId: "other" }, { eventDate: "2026-11-15" }, { period: "first_half" }, { line: 6.5 }, { settlementRuleId: null }, { settlementRuleId: "rules:v2" }, { playerId: "other" }, { stat: "pts" as const }]) {
      expect(comparability({ market: m, quote: q }, { market: { ...m, ...change }, quote: q }, now).comparable).toBe(false);
    }
  });

  it("stale, sample, unknown-fee and entry-pick quotes are never comparable; unknown expiry falls back to age", () => {
    const m = market();
    const q = quote();
    const changes: Partial<Quote>[] = [
      { expiresAt: "2026-11-17T02:00:10.000Z" },
      { provenance: "sample" },
      { price: { kind: "binary", askCents: 45, fee: { status: "unknown" } } },
      { provider: "prizepicks", price: { kind: "entry-pick" } },
      { side: "no" },
    ];
    for (const change of changes) expect(comparability({ market: m, quote: q }, { market: m, quote: { ...q, ...change } }, now).comparable).toBe(false);
    expect(freshness({ ...q, expiresAt: null }, now)).toBe("current");
    expect(freshness({ ...q, expiresAt: null }, now + 20 * 60_000)).toBe("stale");
    expect(freshness({ ...q, observedAt: "not a time" }, now)).toBe("unknown");
    expect(freshness({ ...q, provenance: "manual", expiresAt: null }, now + 20 * 60_000)).toBe("stale");
  });
});

describe("per-record catalog validation", () => {
  it("each bad record is reported with its index, id and reason; valid ones are kept", () => {
    const b = buildCatalog({
      markets: [market(), market({ id: "dup-identity" }), market({ id: "bad-stat", stat: "blk" as never }), market({ id: "naive", eventDate: "2026-02-30" }), { nonsense: true }],
      quotes: [
        quote(),
        quote({ id: "dup-slot" }),
        quote({ id: "bad-odds", provider: "draftkings", price: { kind: "sportsbook", americanOdds: 50, fee: { status: "unknown" } } }),
        quote({ id: "kind-mismatch", provider: "draftkings" }),
        quote({ id: "naive-time", side: "no", observedAt: "2026-11-17 02:00:00" }),
        quote({ id: "pick-with-price", provider: "prizepicks", price: { kind: "entry-pick", askCents: 40 } as never }),
        quote({ id: "unknown-provider", provider: "somebook" }),
      ],
    });
    expect(b.catalog.markets.map((m) => m.id)).toEqual(["market-from-export"]);
    expect(b.catalog.quotes.map((q) => q.id)).toEqual(["quote-1"]);
    const reasons = Object.fromEntries(b.rejected.map((r) => [`${r.kind}:${r.id ?? r.index}`, r.reason]));
    expect(reasons["market:dup-identity"]).toMatch(/same event, player/);
    expect(reasons["market:bad-stat"]).toMatch(/stat must be/);
    expect(reasons["market:naive"]).toMatch(/real YYYY-MM-DD/);
    expect(reasons["market:4"]).toMatch(/id missing/);
    expect(reasons["quote:dup-slot"]).toMatch(/already on the board/);
    expect(reasons["quote:bad-odds"]).toMatch(/americanOdds/);
    expect(reasons["quote:kind-mismatch"]).toMatch(/sportsbook provider cannot carry a binary/);
    expect(reasons["quote:naive-time"]).toMatch(/with a zone/);
    expect(reasons["quote:pick-with-price"]).toMatch(/no price of its own/);
    expect(reasons["quote:unknown-provider"]).toMatch(/not in the registry/);
  });

  it("season 2025 (the frozen holdout) is refused; the seasons either side are accepted", () => {
    const b = buildCatalog({ markets: [market({ id: "h1", eventDate: "2025-11-01" }), market({ id: "h2", eventDate: "2026-04-10" }), market({ id: "ok1", eventDate: "2024-11-04" }), market({ id: "ok2", eventDate: "2026-10-30" })], quotes: [] });
    expect(b.catalog.markets.map((m) => m.id)).toEqual(["ok1", "ok2"]);
    expect(b.rejected.every((r) => r.reason.includes("holdout"))).toBe(true);
  });

  it("the provider set is open: any export book id joins as a sportsbook; known ids keep their kind", () => {
    const p = providersFor(["draftkings", "fanduel", "BetMGM", "espnbet", "bad id!"]);
    expect(p.map((x) => x.id)).toEqual(["kalshi", "draftkings", "prizepicks", "fanduel", "betmgm", "espnbet"]);
    expect(p.find((x) => x.id === "fanduel")?.kind).toBe("sportsbook");
    expect(p.find((x) => x.id === "prizepicks")?.kind).toBe("entry");
    const b = buildCatalog({ providers: p, markets: [market()], quotes: [quote({ id: "fd", provider: "fanduel", price: { kind: "sportsbook", americanOdds: -110, fee: { status: "known", centsPerDollarPayout: 0, basis: "provider" } } })] });
    expect(b.rejected).toEqual([]);
  });
});

describe("partial availability", () => {
  it("a missing provider or side is 'unavailable', and the rest of the board and workspace stay usable", () => {
    const b = buildCatalog({ markets: [market()], quotes: [quote()] });
    const yes = boardFor(b.catalog, "market-from-export", "yes");
    expect(yes.map((s) => s.state)).toEqual(["available", "unavailable", "unavailable"]);
    expect(yes[1]).toEqual({ state: "unavailable", provider: "draftkings", marketId: "market-from-export", side: "yes" });
    expect(quoteSlot(b.catalog, "market-from-export", "kalshi", oppositeSide("yes")).state).toBe("unavailable");
    const m = b.catalog.markets[0] as Market;
    const q = b.catalog.quotes[0] as Quote;
    const ws = { ...createSlipWorkspace(), draft: { ...emptyDraft(), legs: [makeLeg(q, m)] } };
    expect(validateSlipWorkspace(ws).ok).toBe(true);
    expect(evaluateDraft(ws.draft, { now }).complete).toBe(true);
  });

  it("no count of markets, providers or quotes is required (empty is a valid board)", () => {
    const b = buildCatalog({ markets: [], quotes: [] });
    expect(b.rejected).toEqual([]);
    expect(b.catalog.providers).toEqual(KNOWN_PROVIDERS);
    expect(boardFor(b.catalog, "anything", "no").every((s) => s.state === "unavailable")).toBe(true);
  });
});
