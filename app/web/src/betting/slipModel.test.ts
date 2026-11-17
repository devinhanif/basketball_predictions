// Ported from the donor kit's tests/slip-model.test.mjs (20 cases; exact numeric fixtures kept), then new cases for the
// injected catalog, unknown fees, stale prices, entry picks and immutable snapshots.
import { describe, expect, it } from "vitest";
import { buildCatalog, refreshQuotes } from "./catalog";
import { sampleCatalog, sampleMarketId, sampleQuote, buildDemoDraft, buildNBASampleDraft, SAMPLE_STAMP } from "./sample";
import {
  activeLegs,
  addQuote,
  createSlipWorkspace,
  describeOverlaps,
  emptyDraft,
  evaluateDraft,
  legCost,
  legPayout,
  makeHedgeLeg,
  makeLeg,
  markPlaced,
  normalisedCost,
  recordOutcomes,
  saveIdea,
  loadIdea,
  setLegAmount,
  toggleLeg,
  validateSlipWorkspace,
  putManualQuote,
} from "./slipModel";
import type { Market, Quote, Side, SlipDraft, SlipLeg, SlipWorkspace } from "./types";

const NOW = Date.parse(SAMPLE_STAMP) + 1000;
const ev = (d: SlipDraft) => evaluateDraft(d, { now: NOW });
const close = (actual: number, expected: number): void => {
  expect(Math.abs(actual - expected)).toBeLessThan(1e-5);
};
const cents = (q: Quote): number => {
  const r = normalisedCost(q);
  if (!r.ok) throw new Error(r.text);
  return r.cents;
};
const leg = (short: string, provider: string, side: Side, role: SlipLeg["role"] = "planned", amount?: number | null): SlipLeg => {
  const { quote, market } = sampleQuote(short, provider, side);
  return makeLeg(quote, market, role, amount === undefined ? {} : { amount, ...(role === "existing" ? { placedAt: SAMPLE_STAMP } : {}) });
};
const pp = (short: string, side: Side = "yes"): SlipLeg => leg(short, "prizepicks", side);
const nets = (d: SlipDraft): number[] => ev(d).rows.map((r) => r.net);
const unwrap = <T extends object>(r: ({ ok: true } & T) | { ok: false; text: string }): T => {
  if (!r.ok) throw new Error(r.text);
  return r;
};
const withLegs = (legs: SlipLeg[], extra: Partial<SlipDraft> = {}): SlipDraft => ({ ...emptyDraft(), legs, ...extra });

describe("donor slip-model cases", () => {
  it("the labelled sample covers four markets, three providers and both sides (no count is required elsewhere)", () => {
    const cat = sampleCatalog();
    expect(cat.markets.length).toBe(4);
    expect(cat.quotes.length).toBe(24);
    expect(new Set(cat.quotes.map((q) => q.id)).size).toBe(24);
    expect(cat.quotes.every((q) => q.provenance === "sample")).toBe(true);
    expect(validateSlipWorkspace(createSlipWorkspace()).ok).toBe(true);
  });

  it("same payout comparison includes exchange fees and converts American odds (53c vs 54.954955c)", () => {
    close(cents(sampleQuote("collin", "kalshi", "no").quote), 53);
    close(cents(sampleQuote("collin", "draftkings", "no").quote), 54.954955);
    close(cents(sampleQuote("collin", "draftkings", "yes").quote), 40);
    close(cents(sampleQuote("collin", "draftkings", "no").quote) - cents(sampleQuote("collin", "kalshi", "no").quote), 1.954955);
    const p = normalisedCost(sampleQuote("collin", "prizepicks", "yes").quote);
    expect(p.ok).toBe(false);
    if (!p.ok) expect(p.code).toBe("entry_only");
  });

  it("a sportsbook stake pays principal plus winnings for either odds sign", () => {
    const positive = leg("collin", "draftkings", "yes", "planned", 40);
    expect(legCost(positive)).toBe(40);
    expect(legPayout(positive)).toBe(100);
    const negative = leg("collin", "draftkings", "no", "planned", 122);
    expect(legCost(negative)).toBe(122);
    expect(legPayout(negative)).toBe(222);
  });

  it("the demo gives exact payouts ($93 cost, $100 either way, net +$7) but no coverage claim: settlement is unverified", () => {
    const r = ev(buildDemoDraft());
    expect(r.complete).toBe(true);
    expect(r.totalCost).toBe(93);
    expect(r.existingWorst).toBe(-40);
    expect(r.rows.length).toBe(2);
    expect(r.rows.every((x) => x.payout === 100 && x.net === 7)).toBe(true);
    expect(r.providerTotals).toEqual({ draftkings: 40, kalshi: 53 });
    expect(r.warnings.some((w) => w.includes("settlement"))).toBe(true);
    expect(r.coverage.shown).toBe(false);
    if (!r.coverage.shown) expect(r.coverage.reason).toBe("unverified_settlement");
  });

  it("unequal amounts expose residual payout and keep downside (-$16.50 / +$33.50 on $66.50)", () => {
    const d0 = buildDemoDraft();
    const d = unwrap(setLegAmount(d0, d0.legs[1]?.id ?? "", 50)).draft;
    expect(Math.min(...nets(d))).toBe(-16.5);
    expect(Math.max(...nets(d))).toBe(33.5);
    expect(ev(d).totalCost).toBe(66.5);
  });

  it("a sportsbook fee is charged per $1 of gross payout", () => {
    const d0 = buildDemoDraft();
    const first = d0.legs[0] as SlipLeg;
    const q: Quote = { ...first.quote, price: { kind: "sportsbook", americanOdds: 150, fee: { status: "known", centsPerDollarPayout: 2, basis: "assumption" } } };
    const withFee = makeLeg(q, first.market, "existing", { amount: 40, placedAt: SAMPLE_STAMP });
    expect(legCost(withFee)).toBe(42);
    expect(cents(q)).toBe(42);
    expect(Math.min(...nets({ ...d0, legs: [withFee, d0.legs[1] as SlipLeg] }))).toBe(5);
  });

  it("switched-off planned legs are excluded, recorded exposure is never hidden", () => {
    const d0 = buildDemoDraft();
    const off = unwrap(toggleLeg(d0, d0.legs[1]?.id ?? "")).draft;
    const refused = toggleLeg(off, off.legs[0]?.id ?? "");
    expect(refused.ok).toBe(false);
    const r = ev(off);
    expect(r.totalCost).toBe(40);
    expect(Math.min(...nets(off))).toBe(-40);
    expect(Math.max(...nets(off))).toBe(60);
    expect(r.existingWorst).toBe(-40);
  });

  it("an incomplete entry blocks the table and the coverage summary", () => {
    const d = { ...buildDemoDraft(), legs: [...buildDemoDraft().legs, pp("bobby")] };
    const r = ev(d);
    expect(r.complete).toBe(false);
    expect(r.rows).toEqual([]);
    expect(r.coverage.shown).toBe(false);
    expect(r.existingWorst).toBe(-40);
    expect(r.totalCost).toBe(103);
    expect(r.warnings.some((w) => w.includes("at least two"))).toBe(true);
  });

  it("an entry is priced once and pays only when every grouped pick wins (2 picks, $10, 3x)", () => {
    const d = withLegs([pp("bobby"), pp("oso")], { entry: { stake: 10, multiplier: 3 } });
    const r = ev(d);
    expect(r.complete).toBe(true);
    expect(r.totalCost).toBe(10);
    expect(r.rows.length).toBe(4);
    expect(r.rows.filter((x) => x.payout === 30).length).toBe(1);
    expect(Math.max(...nets(d))).toBe(20);
    expect(Math.min(...nets(d))).toBe(-10);
    expect(r.groups.find((g) => g.provider === "prizepicks")?.cost).toBe(10);
    // Adapted: the donor returned 0 for a pick's cost and payout; here a pick has none of its own (null).
    expect(legCost(d.legs[0] as SlipLeg)).toBeNull();
    expect(legPayout(d.legs[0] as SlipLeg)).toBeNull();
  });

  it("duplicate or opposing picks in one market are not a valid entry", () => {
    const r = ev(withLegs([pp("bobby", "yes"), pp("bobby", "no")]));
    expect(r.complete).toBe(false);
    expect(r.coverage.shown).toBe(false);
    expect(r.warnings.some((w) => w.includes("different markets"))).toBe(true);
    const viaAdd = addQuote(withLegs([pp("bobby", "yes")]), sampleQuote("bobby", "prizepicks", "no").quote, sampleQuote("bobby", "prizepicks", "no").market);
    expect(viaAdd.ok).toBe(false);
  });

  it("one entry cannot mix recorded and planned picks", () => {
    const first = pp("bobby");
    const d = withLegs([{ ...first, role: "existing", placedAt: SAMPLE_STAMP }, pp("oso")]);
    const r = ev(d);
    expect(r.complete).toBe(false);
    expect(r.existingWorst).toBeNull();
  });

  it("same-game markets keep every combination with no assumed relationship", () => {
    const d = withLegs(["collin", "bobby", "oso", "jalen"].map((s) => leg(s, "kalshi", "yes")));
    const r = ev(d);
    expect(r.rows.length).toBe(16);
    expect(r.overlaps.filter((o) => o.kind === "same-event").length).toBe(1);
    expect(r.rows.some((x) => Object.values(x.outcomes).every((s) => s === "no"))).toBe(true);
  });

  it("legs, ideas and validated copies do not share references with the board or the draft", () => {
    const cat = sampleCatalog();
    const q = cat.quotes[0] as Quote;
    const m = cat.markets.find((x) => x.id === q.marketId) as Market;
    const l = makeLeg(q, m);
    expect(l.quote).not.toBe(q);
    expect(() => {
      (l.quote as { id: string }).id = "changed";
    }).toThrow(TypeError);
    const ws: SlipWorkspace = { ...createSlipWorkspace(), draft: withLegs([l]) };
    const saved = unwrap(saveIdea(ws, { now: SAMPLE_STAMP, id: "idea-1" })).workspace;
    const edited = unwrap(setLegAmount(saved.draft, l.id, 88)).draft;
    expect(saved.ideas[0]?.legs[0]?.amount).toBe(2);
    expect(edited.legs[0]?.amount).toBe(88);
    const copy = unwrap(validateSlipWorkspace(saved)).workspace;
    expect(copy).not.toBe(saved);
    expect(Object.isFrozen(copy.draft.legs[0]?.quote)).toBe(true);
  });

  it("invalid stored prices, amounts, ids and tags fail validation with the path that failed", () => {
    const base = (): Record<string, unknown> => JSON.parse(JSON.stringify({ ...createSlipWorkspace(), draft: withLegs([leg("bobby", "kalshi", "yes")]) })) as Record<string, unknown>;
    const manual = { ...sampleQuote("bobby", "kalshi", "yes").quote, id: "m1", provenance: "manual" };
    const mutations: [string, (w: Record<string, unknown>) => void][] = [
      ["manualQuotes[0]", (w) => (w["manualQuotes"] = [{ ...manual, price: { kind: "binary", askCents: -1, fee: { status: "unknown" } } }])],
      ["manualQuotes", (w) => (w["manualQuotes"] = [manual, manual])],
      ["draft.legs[0].amount", (w) => ((w["draft"] as { legs: [{ amount: number }] }).legs[0].amount = 0.5)],
      ["draft.entry", (w) => ((w["draft"] as { entry: { multiplier: number } }).entry.multiplier = 0)],
      ["draft.tags", (w) => ((w["draft"] as { tags: unknown[] }).tags = [17])],
      ["draft.legs", (w) => ((w["draft"] as { legs: unknown[] }).legs.push((w["draft"] as { legs: unknown[] }).legs[0]))],
    ];
    for (const [path, mutate] of mutations) {
      const w = base();
      mutate(w);
      const r = validateSlipWorkspace(w);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.text.startsWith(path)).toBe(true);
    }
  });

  it("non-finite or invalid American odds never produce a cost", () => {
    for (const odds of [0, 99, -99, Infinity, NaN]) {
      const { quote, market } = sampleQuote("collin", "draftkings", "yes");
      const q: Quote = { ...quote, price: { kind: "sportsbook", americanOdds: odds, fee: { status: "known", centsPerDollarPayout: 0, basis: "assumption" } } };
      expect(normalisedCost(q).ok).toBe(false);
      expect(ev(withLegs([makeLeg(q, market)])).complete).toBe(false);
    }
  });

  it("a settled idea needs an outcome for every active market", () => {
    const d = withLegs([leg("bobby", "kalshi", "yes"), leg("oso", "kalshi", "yes")]);
    const ws = unwrap(saveIdea({ ...createSlipWorkspace(), draft: d }, { now: SAMPLE_STAMP, id: "idea" })).workspace;
    expect(recordOutcomes(ws, "idea", { [sampleMarketId("bobby")]: "yes" }, "settled").ok).toBe(false);
    const done = unwrap(recordOutcomes(ws, "idea", { [sampleMarketId("bobby")]: "yes", [sampleMarketId("oso")]: "no" }, "settled")).workspace;
    expect(validateSlipWorkspace(done).ok).toBe(true);
    const forged = JSON.parse(JSON.stringify(done)) as { ideas: [{ outcomes: Record<string, string> }] };
    forged.ideas[0].outcomes = { [sampleMarketId("bobby")]: "yes" };
    expect(validateSlipWorkspace(forged).ok).toBe(false);
  });

  it("missing or invalid data is refused, not replaced by an empty or sample workspace", () => {
    for (const bad of [undefined, null, {}, { draft: emptyDraft() }]) expect(validateSlipWorkspace(bad).ok).toBe(false);
    const a = createSlipWorkspace();
    const b = createSlipWorkspace();
    expect(a).not.toBe(b);
    expect(a.draft.legs.length).toBe(0);
    expect(a.manualQuotes.length).toBe(0);
  });

  it("a provider's maximum payout counts mutually exclusive opposing legs once", () => {
    const d = withLegs([leg("collin", "kalshi", "yes", "planned", 100), leg("collin", "kalshi", "no", "planned", 60)]);
    const r = ev(d);
    const k = r.groups.find((g) => g.provider === "kalshi");
    expect(k?.maxPayout).toBe(100);
    expect(Math.max(...r.rows.map((x) => x.payout))).toBe(100);
  });

  it("a 1x entry is valid break-even modelling, not a positive return", () => {
    const d = withLegs([pp("bobby"), pp("oso")], { entry: { stake: 10, multiplier: 1 } });
    expect(ev(d).complete).toBe(true);
    expect(Math.max(...nets(d))).toBe(0);
    expect(Math.min(...nets(d))).toBe(-10);
  });

  it("the NBA sample is one planned Jalen Green DraftKings YES, $10 at +145, $24.50 gross", () => {
    const d = buildNBASampleDraft();
    expect(d.title).toBe("NBA sample · build your idea");
    expect(d.thesis).toMatch(/Sample/);
    expect(d.origin).toBe("sample");
    expect(d.legs.length).toBe(1);
    const l = d.legs[0] as SlipLeg;
    expect(l.quote.marketId).toBe(sampleMarketId("jalen"));
    expect(l.quote.provider).toBe("draftkings");
    expect(l.quote.side).toBe("yes");
    expect(l.quote.price).toMatchObject({ kind: "sportsbook", americanOdds: 145 });
    expect(l.quote.provenance).toBe("sample");
    expect(l.amount).toBe(10);
    expect(l.role).toBe("planned");
    expect(l.enabled).toBe(true);
    expect(legPayout(l)).toBe(24.5);
    const r = ev(d);
    expect(r.totalCost).toBe(10);
    expect(Math.max(...nets(d))).toBe(14.5);
    expect(Math.min(...nets(d))).toBe(-10);
    expect(r.existingWorst).toBe(0);
    expect(createSlipWorkspace().draft.legs.length).toBe(0);
    expect(buildNBASampleDraft().legs[0]?.quote).not.toBe(l.quote);
  });
});

// ------------------------------------------------------------------------------------------------ new cases

const T0 = "2026-11-17T00:00:00.000Z";
const t0 = Date.parse(T0);
const realMarket = (over: Partial<Market> = {}): Market => ({
  id: "0022600211:1630178:pts:24.5",
  eventId: "0022600211",
  eventDate: "2026-11-16",
  playerId: "1630178",
  stat: "pts",
  line: 24.5,
  period: "full_game",
  settlementRuleId: null,
  playerName: "Test Player",
  eventLabel: "AAA @ BBB",
  origin: "manual",
  ...over,
});
const liveQuote = (over: Partial<Quote> = {}): Quote => ({
  id: "q-live",
  marketId: realMarket().id,
  provider: "kalshi",
  side: "yes",
  price: { kind: "binary", askCents: 44, fee: { status: "known", centsPerDollarPayout: 2, basis: "provider" } },
  provenance: "live",
  observedAt: T0,
  expiresAt: null,
  ...over,
});

describe("injected catalog, end to end with arbitrary ids", () => {
  it("non-sample player, market, event and book ids flow through slip, placement, hedge, idea and validation", () => {
    const m = realMarket({ settlementRuleId: "rules:test-v1" });
    const built = buildCatalog({
      providers: [
        { id: "kalshi", label: "Kalshi", kind: "exchange" },
        { id: "fanduel", label: "fanduel", kind: "sportsbook" },
      ],
      markets: [m],
      quotes: [
        liveQuote(),
        liveQuote({ id: "q-fd-no", provider: "fanduel", side: "no", price: { kind: "sportsbook", americanOdds: -120, fee: { status: "known", centsPerDollarPayout: 0, basis: "provider" } } }),
        liveQuote({ id: "q-k-no", side: "no", price: { kind: "binary", askCents: 58, fee: { status: "known", centsPerDollarPayout: 2, basis: "provider" } } }),
      ],
    });
    expect(built.rejected).toEqual([]);
    const fdNo = built.catalog.quotes.find((q) => q.id === "q-fd-no") as Quote;
    let d = unwrap(addQuote(emptyDraft(), fdNo, m, { amount: 12, id: "leg-fd" })).draft;
    d = markPlaced(d, T0);
    expect(d.legs[0]).toMatchObject({ id: "leg-fd", role: "existing", amount: 12, placedAt: T0 });
    const kYes = built.catalog.quotes.find((q) => q.id === "q-live") as Quote;
    const hedge = unwrap(makeHedgeLeg(d, kYes, m, { id: "leg-k" }));
    expect(hedge.leg.amount).toBe(22); // $12 at -120 pays $22 gross: 22 contracts
    d = { ...d, legs: [...d.legs, hedge.leg] };
    const r = evaluateDraft(d, { now: t0 + 60_000 });
    expect(r.complete).toBe(true);
    expect(r.rows.length).toBe(2);
    expect(r.coverage.shown).toBe(true);
    const ws = unwrap(saveIdea({ ...createSlipWorkspace(), draft: d }, { now: T0, id: "idea-real" })).workspace;
    expect(validateSlipWorkspace(ws).ok).toBe(true);
    expect(unwrap(loadIdea(ws, "idea-real")).workspace.draft.legs.map((l) => l.id)).toEqual(["leg-fd", "leg-k"]);
  });
});

describe("unknown fees, stale prices, entry picks, refresh", () => {
  it("an unknown fee stays unknown: no normalised cost, no leg cost, no table, no coverage", () => {
    const q = liveQuote({ price: { kind: "binary", askCents: 44, fee: { status: "unknown" } } });
    const c = normalisedCost(q);
    expect(c.ok).toBe(false);
    if (!c.ok) expect(c.code).toBe("fee_unknown");
    const l = makeLeg(q, realMarket());
    expect(legCost(l)).toBeNull();
    const r = evaluateDraft(withLegs([l]), { now: t0 });
    expect(r.totalCost).toBeNull();
    expect(r.rows).toEqual([]);
    expect(r.coverage).toMatchObject({ shown: false, reason: "incomplete" });
    expect(r.warnings.some((w) => w.includes("fee is unknown"))).toBe(true);
  });

  it("a stale planned price suppresses coverage; a fresh one shows it; a recorded entry price never goes stale", () => {
    const d = withLegs([makeLeg(liveQuote(), realMarket())]);
    expect(evaluateDraft(d, { now: t0 + 60_000 }).coverage.shown).toBe(true);
    const stale = evaluateDraft(d, { now: t0 + 16 * 60_000 });
    expect(stale.coverage).toMatchObject({ shown: false, reason: "stale_quote" });
    expect(stale.coverage.shown ? "" : stale.coverage.text).toMatch(/^Not shown, because/);
    expect(evaluateDraft(withLegs([makeLeg(liveQuote({ expiresAt: "2026-11-17T00:00:30.000Z" }), realMarket())]), { now: t0 + 60_000 }).coverage.shown).toBe(false);
    expect(evaluateDraft(withLegs([makeLeg(liveQuote({ observedAt: "2026-11-17T01:00:00.000Z" }), realMarket())]), { now: t0 }).coverage.shown).toBe(false);
    const placed = markPlaced(d, T0);
    expect(evaluateDraft(placed, { now: t0 + 86_400_000 }).coverage.shown).toBe(true);
  });

  it("two lines on the same player and stat are not summarised as coverage", () => {
    const a = realMarket({ id: "m-a" });
    const b = realMarket({ id: "m-b", line: 20.5 });
    const d = withLegs([makeLeg(liveQuote({ marketId: "m-a" }), a), makeLeg(liveQuote({ id: "q2", marketId: "m-b", side: "no" }), b)]);
    const r = evaluateDraft(d, { now: t0 });
    expect(r.rows.length).toBe(4);
    expect(r.coverage).toMatchObject({ shown: false, reason: "related_markets" });
    expect(r.overlaps.some((o) => o.kind === "same-player")).toBe(true);
  });

  it("an entry pick never yields a per-pick price, cost or payout", () => {
    const { quote, market } = sampleQuote("oso", "prizepicks", "yes");
    expect(quote.price).toEqual({ kind: "entry-pick" });
    expect(normalisedCost(quote)).toMatchObject({ ok: false, code: "entry_only" });
    const l = makeLeg(quote, market);
    expect(l.amount).toBeNull();
    expect(legCost(l)).toBeNull();
    expect(legPayout(l)).toBeNull();
    expect(setLegAmount(withLegs([l]), l.id, 5).ok).toBe(false);
  });

  it("a quote refresh moves the board only: planned legs, recorded positions and saved ideas keep their prices", () => {
    const m = realMarket();
    const first = buildCatalog({ markets: [m], quotes: [liveQuote()] }).catalog;
    const q = first.quotes[0] as Quote;
    const d = markPlaced(withLegs([makeLeg(q, m, "planned", { id: "l1" })]), T0);
    const ws = unwrap(saveIdea({ ...createSlipWorkspace(), draft: d }, { now: T0, id: "i1" })).workspace;
    const next = refreshQuotes(first, [liveQuote({ price: { kind: "binary", askCents: 71, fee: { status: "known", centsPerDollarPayout: 2, basis: "provider" } } })]).catalog;
    expect(next.quotes[0]?.price).toMatchObject({ askCents: 71 });
    expect(first.quotes[0]?.price).toMatchObject({ askCents: 44 });
    expect(ws.draft.legs[0]?.quote.price).toMatchObject({ askCents: 44 });
    expect(ws.ideas[0]?.legs[0]?.quote.price).toMatchObject({ askCents: 44 });
    expect(Object.isFrozen(ws.ideas[0])).toBe(true);
    expect(() => {
      (ws.ideas[0]?.legs[0]?.quote.price as { askCents: number }).askCents = 1;
    }).toThrow(TypeError);
    expect(() => {
      (first.quotes[0] as { side: string }).side = "no";
    }).toThrow(TypeError);
  });

  it("re-adding the same planned quote re-enables it instead of duplicating", () => {
    const { quote, market } = sampleQuote("bobby", "kalshi", "yes");
    const d1 = unwrap(addQuote(emptyDraft(), quote, market)).draft;
    const off = unwrap(toggleLeg(d1, d1.legs[0]?.id ?? "")).draft;
    const d2 = unwrap(addQuote(off, quote, market)).draft;
    expect(d2.legs.length).toBe(1);
    expect(d2.legs[0]?.enabled).toBe(true);
    expect(d2.legs[0]?.id).toBe(d1.legs[0]?.id);
  });

  it("markPlaced records only switched-on planned legs and keeps ids, quotes, sides and amounts", () => {
    const a = leg("bobby", "kalshi", "yes", "planned", 7);
    const b = leg("oso", "draftkings", "no", "planned", 12.5);
    const d = unwrap(toggleLeg(withLegs([a, b]), b.id)).draft;
    const placed = markPlaced(d, T0);
    expect(placed.legs[0]).toMatchObject({ id: a.id, role: "existing", amount: 7, placedAt: T0, quote: a.quote });
    expect(placed.legs[0]?.quote.side).toBe("yes");
    expect(placed.legs[1]).toMatchObject({ id: b.id, role: "planned", enabled: false, placedAt: null });
    expect(activeLegs(placed).map((l) => l.id)).toEqual([a.id]);
  });

  it("overlaps are words only: no score, weight or probability field", () => {
    const o = describeOverlaps([leg("oso", "kalshi", "yes"), leg("jalen", "kalshi", "yes"), leg("oso", "draftkings", "no")]);
    expect(o.map((x) => x.kind).sort()).toEqual(["same-event", "same-market"]);
    for (const x of o) expect(Object.keys(x).sort()).toEqual(["detail", "kind", "label", "marketIds"]);
  });

  it("a typed price is stored as a manual snapshot and never touches legs", () => {
    const l = leg("bobby", "kalshi", "yes");
    const ws: SlipWorkspace = { ...createSlipWorkspace(), draft: withLegs([l]) };
    const typed = { ...sampleQuote("bobby", "kalshi", "yes").quote, id: "typed-1", provenance: "manual", observedAt: T0 };
    const next = unwrap(putManualQuote(ws, typed)).workspace;
    expect(next.manualQuotes.length).toBe(1);
    expect(next.draft.legs[0]).toBe(l);
    expect(putManualQuote(ws, { ...typed, provenance: "live" }).ok).toBe(false);
  });
});
