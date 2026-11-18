// The screens' view-model and store, tested at the logic level (no DOM library here): the donor kit's slip, compare and
// hedge UI cases with their exact fixtures, plus the rules the screens must keep (stale prices hide coverage, an entry
// venue never shows a per-pick price, no cheapest mark on unverified settlement, unusable stored quotes never priced).
import { describe, expect, it } from "vitest";
import { buildCatalog, KNOWN_PROVIDERS } from "./catalog";
import { evaluateDraft, normalisedCost } from "./slipModel";
import { sampleCatalog, sampleQuote, SAMPLE_STAMP } from "./sample";
import { betActions, createBetStore } from "./store";
import { centsFromAmerican, exportMarketId, marketFromFacts, providerRegistry, storedBoard, type ApiBoard } from "./snapshots";
import { compareHash, compareHashFor, hedgeCards, hedgeSideFor, heldPositions, lowestCostCrown, parseCompareQuery, typedQuote, venueCards } from "./viewModel";
import type { StorageLike } from "./repository";
import type { Market, ProviderInfo } from "./types";
import { instantMs } from "../time";

const NOW = instantMs(SAMPLE_STAMP);
const memory = (): StorageLike => {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) };
};
const open = () => {
  const store = createBetStore({ storage: memory(), scope: { kind: "device" }, nowIso: () => SAMPLE_STAMP });
  return { store, actions: betActions(store, () => SAMPLE_STAMP) };
};
const kalshi = KNOWN_PROVIDERS[0] as ProviderInfo;
const dk = KNOWN_PROVIDERS[1] as ProviderInfo;
const pp = KNOWN_PROVIDERS[2] as ProviderInfo;

describe("compare: the donor's equal-payout cost fixture", () => {
  it("DK NO -122 costs 54.954955c per $1; Kalshi NO 52c + 1c costs 53c", () => {
    const c = sampleCatalog();
    const dkNo = c.quotes.find((q) => q.id === "collin-draftkings-no");
    const kNo = c.quotes.find((q) => q.id === "collin-kalshi-no");
    if (!dkNo || !kNo) throw new Error("sample missing");
    expect(normalisedCost(dkNo)).toEqual({ ok: true, cents: 54.954955 });
    expect(normalisedCost(kNo)).toEqual({ ok: true, cents: 53 });
  });

  it("venue cards are in name sequence, PrizePicks has no per-pick price, and no cheapest mark on unverified settlement", async () => {
    const { store } = open();
    await store.load();
    const ws = store.get().workspace;
    if (!ws) throw new Error("no workspace");
    const market = sampleCatalog().markets.find((m) => m.id === "sample-collin") as Market;
    const cards = venueCards({ providers: KNOWN_PROVIDERS, stored: sampleCatalog(), workspace: ws, market, side: "no", now: NOW });
    expect(cards.map((c) => c.provider.label)).toEqual(["DraftKings", "Kalshi", "PrizePicks"]);
    const pick = cards.find((c) => c.provider.id === "prizepicks");
    expect(pick?.cost).toMatchObject({ ok: false, code: "entry_only" });
    const crown = lowestCostCrown(cards, market, NOW);
    expect(crown.shown).toBe(false);
    if (!crown.shown) expect(crown.reasons.join(" ")).toMatch(/Settlement rules/);
  });

  it("a cheapest mark appears only when every pair is like-for-like (verified rule, known fees, current prices)", () => {
    const m: Market = { ...(sampleCatalog().markets[0] as Market), id: "m1", origin: "manual", settlementRuleId: "rule-1" };
    const now = Date.parse("2024-11-05T02:05:00.000Z");
    const q = (provider: ProviderInfo, price: string) => {
      const r = typedQuote(provider, m, "yes", { price, fee: "0" }, "2024-11-05T02:00:00.000Z", `${provider.id}-q`);
      if (!r.ok) throw new Error(r.text);
      return r.quote;
    };
    const ws = { draft: { legs: [], entry: { stake: 10, multiplier: 3 }, title: "", thesis: "", tags: [], origin: "user" as const }, ideas: [], manualQuotes: [q(kalshi, "53"), q(dk, "-122")], picks: [] };
    const cards = venueCards({ providers: [kalshi, dk], stored: null, workspace: ws, market: m, side: "yes", now });
    expect(lowestCostCrown(cards, m, now)).toEqual({ shown: true, provider: "kalshi" });
  });

  it("a typed price keeps an unknown fee unknown, labels a typed fee as the viewer's entry, and refuses odds between -100 and 100", () => {
    const m = sampleCatalog().markets[0] as Market;
    const blank = typedQuote(dk, m, "yes", { price: "+145", fee: "" }, SAMPLE_STAMP);
    expect(blank.ok && blank.quote.price).toEqual({ kind: "sportsbook", americanOdds: 145, fee: { status: "unknown" } });
    const typed = typedQuote(kalshi, m, "no", { price: "52", fee: "1" }, SAMPLE_STAMP);
    expect(typed.ok && typed.quote.price).toEqual({ kind: "binary", askCents: 52, fee: { status: "known", centsPerDollarPayout: 1, basis: "assumption" } });
    expect(typed.ok && typed.quote.provenance).toBe("manual");
    expect(typedQuote(dk, m, "yes", { price: "50", fee: "" }, SAMPLE_STAMP).ok).toBe(false);
    const entry = typedQuote(pp, m, "yes", { price: "", fee: "" }, SAMPLE_STAMP);
    expect(entry.ok && entry.quote.price).toEqual({ kind: "entry-pick" });
  });
});

describe("slip to Hedge Finder: the donor's NBA flow", () => {
  it("Jalen DK YES $10 at +145 pays $24.50; add Bobby; mark placed keeps ids, quotes and amounts; Kalshi NO hedge is 25 contracts", async () => {
    const { store, actions } = open();
    await store.load();
    await actions.loadDemo();
    let ws = store.get().workspace;
    const jalen = ws?.draft.legs[0];
    expect(jalen?.market.playerName).toBe("Jalen Green");
    expect(evaluateDraft(ws?.draft ?? { legs: [], entry: { stake: 10, multiplier: 3 }, title: "", thesis: "", tags: [], origin: "user" }, { now: NOW }).groups[0]?.maxPayout).toBe(24.5);
    const bobby = sampleQuote("bobby", "kalshi", "yes");
    await actions.addQuote(bobby.quote, bobby.market);
    await actions.addQuote(bobby.quote, bobby.market);
    ws = store.get().workspace;
    expect(ws?.draft.legs.map((l) => l.market.id)).toEqual(["sample-jalen", "sample-bobby"]);
    const before = ws?.draft.legs.map((l) => ({ id: l.id, quote: l.quote, amount: l.amount }));
    expect((await actions.markPlaced()).ok).toBe(true);
    ws = store.get().workspace;
    if (!ws) throw new Error("no workspace");
    expect(ws.draft.legs.every((l) => l.role === "existing" && l.placedAt === SAMPLE_STAMP)).toBe(true);
    expect(ws.draft.legs.map((l) => ({ id: l.id, quote: l.quote, amount: l.amount }))).toEqual(before);
    const held = heldPositions(ws.draft);
    expect(held.map((l) => l.market.playerName)).toEqual(["Jalen Green", "Bobby Portis"]);
    const leg = held[0];
    if (!leg) throw new Error("no held leg");
    const side = hedgeSideFor(leg);
    expect(side).toBe("no");
    const cards = hedgeCards({ providers: KNOWN_PROVIDERS, stored: sampleCatalog(), workspace: ws, market: leg.market, side, now: NOW });
    const k = cards.find((c) => c.provider.id === "kalshi");
    expect(k?.hedge).toMatchObject({ ok: true, sized: true });
    expect(k?.hedge?.ok && k.hedge.leg.amount).toBe(25);
    expect(k?.hedge?.ok && k.hedge.leg.quote.side).toBe("no");
    const d = cards.find((c) => c.provider.id === "draftkings");
    // DK NO -175: stake rounded up to the cent so the gross payout reaches $24.50.
    expect(d?.hedge?.ok && d.hedge.leg.amount).toBe(15.6);
    const p = cards.find((c) => c.provider.id === "prizepicks");
    // An entry pick pays only with its whole entry: it is never sized as a standalone hedge.
    expect(p?.hedge).toMatchObject({ ok: false });
    expect(p?.hedge?.text).toMatch(/entry pick/);
  });

  it("an arbitrary non-sample player works end to end (export market id, typed prices, placed, hedged)", async () => {
    const { store, actions } = open();
    await store.load();
    const built = marketFromFacts({ gameId: "0022400777", date: "2024-12-02", playerId: 203999, playerName: "Nikola Jokic", stat: "reb", line: 12.5, eventLabel: "DEN @ ATL" });
    if (!built.ok) throw new Error(built.text);
    expect(built.market.id).toBe(exportMarketId("0022400777", 203999, "reb", 12.5));
    await actions.addPick(built.market, "yes", "leg");
    const q = typedQuote(dk, built.market, "yes", { price: "-110", fee: "0" }, SAMPLE_STAMP);
    if (!q.ok) throw new Error(q.text);
    await actions.putTyped(q.quote);
    await actions.addQuote(q.quote, built.market, 22);
    expect(store.get().workspace?.picks.length).toBe(0);
    await actions.markPlaced();
    const ws = store.get().workspace;
    if (!ws) throw new Error("no workspace");
    const leg = heldPositions(ws.draft)[0];
    if (!leg) throw new Error("no leg");
    const no = typedQuote(kalshi, built.market, "no", { price: "47", fee: "2" }, SAMPLE_STAMP);
    if (!no.ok) throw new Error(no.text);
    const cards = hedgeCards({ providers: [kalshi], stored: buildCatalog({ providers: [kalshi], markets: [built.market], quotes: [no.quote] }).catalog, workspace: ws, market: built.market, side: "no", now: NOW });
    // $22 at -110 pays $42 gross: 42 whole contracts.
    expect(cards[0]?.hedge?.ok && cards[0].hedge.leg.amount).toBe(42);
  });
});

describe("coverage on screen comes only from the model's gate", () => {
  it("a stale planned price suppresses the coverage block and says why", () => {
    const yes = sampleQuote("collin", "draftkings", "yes");
    const m: Market = { ...yes.market, settlementRuleId: "rule-1" };
    const t = "2024-11-05T02:00:00.000Z";
    const a = typedQuote(dk, m, "yes", { price: "+150", fee: "0" }, t);
    const b = typedQuote(dk, m, "no", { price: "+110", fee: "0" }, t);
    if (!a.ok || !b.ok) throw new Error("bad");
    const draft = { legs: [], entry: { stake: 10, multiplier: 3 }, title: "", thesis: "", tags: [], origin: "user" as const };
    const withLegs = { ...draft, legs: [{ id: "a", quote: a.quote, market: m, amount: 40, enabled: true, role: "planned" as const, placedAt: null }, { id: "b", quote: b.quote, market: m, amount: 40, enabled: true, role: "planned" as const, placedAt: null }] };
    const fresh = evaluateDraft(withLegs, { now: Date.parse(t) + 60_000 });
    expect(fresh.coverage.shown).toBe(true);
    const stale = evaluateDraft(withLegs, { now: Date.parse(t) + 16 * 60_000 });
    expect(stale.coverage).toMatchObject({ shown: false, reason: "stale_quote" });
    expect(stale.rows.length).toBe(2);
  });
});

describe("stored snapshots (the read-only board contract)", () => {
  const market = (() => {
    const r = marketFromFacts({ gameId: "0022400123", date: "2024-11-20", playerId: 1, playerName: "A Player", stat: "pts", line: 20.5, eventLabel: "AAA @ BBB" });
    if (!r.ok) throw new Error(r.text);
    return r.market;
  })();
  const board = (quotes: ApiBoard["lines"][number]["quotes"], lines = [20.5]): ApiBoard => ({
    game_id: "0022400123", date: "2024-11-20", tip_utc: "2024-11-21T00:30:00Z", final: false, player_id: 1, player: "A Player", team: "AAA", opponent: "BBB", stat: "pts",
    as_of: "2024-11-20T20:00:00Z", data_version: "v", provenance: "delayed",
    lines: lines.map((line) => ({ line, quotes: line === 20.5 ? quotes : [] })), providers_absent: [], model_ref: null, unknown: ["fee"], note: "n",
  });
  const q = (provider: string, side: "over" | "under", price: number | null, usable = true) => ({
    quote_id: `${provider}|${side}`, provider, side, price_american: price, implied_prob_raw: price === null ? null : null, snapshot_utc: "2024-11-20T23:00:00Z", minutes_before_tip: 90, is_alternate: false, usable, unusable_reason: usable ? null : "placeholder",
  });
  const registry = providerRegistry({ as_of: "", data_version: "", unknown: [], note: "", providers: [
    { id: "fanduel", label: "FanDuel", kind: "sportsbook", price_unit: "american", newest_snapshot_utc: null, n_quotes: 1, fee: null, settlement_rule_id: null },
    { id: "polymarket", label: "Polymarket", kind: "exchange", price_unit: "american", newest_snapshot_utc: null, n_quotes: 1, fee: null, settlement_rule_id: null },
  ] });

  it("stored quotes are delayed with an unknown fee; an exchange's American price becomes cents; an unusable quote is never priced", () => {
    const sb = storedBoard(board([q("fanduel", "over", -110), q("polymarket", "over", 150), q("draftkings", "over", null, false), q("fanduel", "under", -110)]), market, "yes", registry);
    expect(sb.rejected).toEqual([]);
    expect(sb.catalog.quotes.map((x) => x.provider).sort()).toEqual(["fanduel", "polymarket"]);
    expect(sb.catalog.quotes.every((x) => x.provenance === "delayed" && x.price.kind !== "entry-pick" && x.price.fee.status === "unknown")).toBe(true);
    expect(sb.catalog.quotes.find((x) => x.provider === "polymarket")?.price).toMatchObject({ kind: "binary", askCents: 40 });
    expect(sb.unusable).toEqual([{ provider: "draftkings", reason: "placeholder" }]);
    expect(centsFromAmerican({ price_american: -122, implied_prob_raw: null })).toBe(54.954955);
  });

  it("an empty board is a designed state, and other stored lines are offered", () => {
    const sb = storedBoard(board([], [19.5, 20.5, 21.5]), market, "yes", registry);
    expect(sb.catalog.quotes).toEqual([]);
    expect(sb.lines).toEqual([19.5, 20.5, 21.5]);
    expect(storedBoard({ ...board([]), lines: [] }, market, "yes", registry).lines).toEqual([]);
  });

  it("holdout-season games and combo stats never become markets", () => {
    expect(marketFromFacts({ gameId: "g", date: "2025-12-01", playerId: 1, playerName: "X", stat: "pts", line: 1.5, eventLabel: "" }).ok).toBe(false);
    expect(marketFromFacts({ gameId: "g", date: "2026-11-16", playerId: 1, playerName: "X", stat: "pra", line: 1.5, eventLabel: "" }).ok).toBe(false);
  });
});

describe("the address", () => {
  it("round-trips a comparison and refuses junk", () => {
    const m = sampleCatalog().markets[0] as Market;
    const h = compareHashFor(m, "no");
    const query = h.split("?")[1] ?? "";
    expect(parseCompareQuery(query)).toMatchObject({ game: m.eventId, player: m.playerId, stat: m.stat, line: m.line, side: "under" });
    expect(parseCompareQuery("game=../../x&line=1e9&side=sideways")).toMatchObject({ game: null, line: null, side: "over" });
    expect(compareHash("hedge", { leg: "leg-1" })).toBe("#/bet/hedge?leg=leg-1");
  });
});
