// Ported from the donor kit's tests/hedge.test.mjs (10 cases, exact fixtures), plus the cross-venue sizing used by the
// Hedge Finder flow and the enumeration cap.
import { describe, expect, it } from "vitest";
import { calculateFixedScenarioHedge, enumerateOutcomes, FIXED_SCENARIO_CANDIDATES, MAX_ENUMERATED_MARKETS, sizeHedge, sizeToPayout, uncoveredPayout } from "./hedgeMath";
import { buildNBASampleDraft, sampleQuote, SAMPLE_STAMP } from "./sample";
import { emptyDraft, evaluateDraft, makeHedgeLeg, makeLeg, markPlaced } from "./slipModel";
import type { Market, Quote, SlipLeg } from "./types";

describe("donor fixed-scenario hedge", () => {
  it("unhedged two-contract position includes the full loss and gain", () => {
    const r = calculateFixedScenarioHedge([]);
    expect(r.scenarios.length).toBe(6);
    expect(r.totalCost).toBe(0.78);
    expect(r.additionalCost).toBe(0);
    expect(r.worstCase).toBe(-0.78);
    expect(r.bestCase).toBe(1.22);
    expect(r.scenarios.filter((s) => s.net === -0.78).length).toBe(4);
    expect(r.scenarios.filter((s) => s.net === 1.22).length).toBe(2);
  });

  it("exact complementary pair returns the supplied hypothetical 16-cent floor", () => {
    const r = calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 52, fee: 1 }]);
    expect(r.totalCost).toBe(1.84);
    expect(r.additionalCost).toBe(1.06);
    expect(r.worstCase).toBe(0.16);
    expect(r.bestCase).toBe(0.16);
    for (const s of r.scenarios) {
      expect(s.payout).toBe(2);
      expect(s.net).toBe(0.16);
      expect(s.winners).toBe(1);
    }
  });

  it("NO 2+ leaves exactly two threes uncovered", () => {
    const gaps = calculateFixedScenarioHedge([{ id: "no2", qty: 2, price: 41, fee: 1 }]).scenarios.filter((s) => s.threes === 2);
    expect(gaps.length).toBe(2);
    for (const g of gaps) {
      expect(g.payout).toBe(0);
      expect(g.net).toBe(-1.62);
      expect(g.winners).toBe(0);
    }
  });

  it("Phoenix win exposure includes outcomes where both positions lose", () => {
    const r = calculateFixedScenarioHedge([{ id: "phx", qty: 2, price: 60, fee: 2 }]);
    const bothLose = r.scenarios.filter((s) => s.threes < 3 && !s.phoenixWins);
    expect(bothLose.length).toBe(2);
    for (const s of bothLose) {
      expect(s.payout).toBe(0);
      expect(s.net).toBe(-2.02);
    }
    expect(r.bestCase).toBe(1.98);
  });

  it("each added per-contract fee reduces every scenario by its quantity", () => {
    const baseline = calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 52, fee: 1 }]);
    const higher = calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 52, fee: 4 }]);
    expect(higher.totalCost).toBe(1.9);
    for (let i = 0; i < 6; i++) expect(higher.scenarios[i]?.net).toBe(0.1);
    expect(Number((higher.totalCost - baseline.totalCost).toFixed(2))).toBe(0.06);
  });

  it("unequal complementary quantities show residual exposure", () => {
    const r = calculateFixedScenarioHedge([{ id: "no3", qty: 1, price: 52, fee: 1 }]);
    expect(r.worstCase).toBe(-0.31);
    expect(r.bestCase).toBe(0.69);
  });

  it("multiple legs add all costs and all qualifying payouts", () => {
    const r = calculateFixedScenarioHedge([
      { id: "no3", qty: 2, price: 52, fee: 1 },
      { id: "phx", qty: 1, price: 60, fee: 2 },
    ]);
    expect(r.totalCost).toBe(2.46);
    expect(r.worstCase).toBe(-0.46);
    expect(r.bestCase).toBe(0.54);
    for (const s of r.scenarios) expect(s.payout).toBe(s.phoenixWins ? 3 : 2);
  });

  it("candidate defaults retain explicit settlement categories", () => {
    expect(FIXED_SCENARIO_CANDIDATES.length).toBe(3);
    expect(FIXED_SCENARIO_CANDIDATES.map(({ id, price, fee }) => ({ id, price, fee }))).toEqual([
      { id: "no3", price: 52, fee: 1 },
      { id: "no2", price: 41, fee: 1 },
      { id: "phx", price: 60, fee: 2 },
    ]);
  });

  it("invalid or nonfinite inputs are rejected before calculation", () => {
    for (const qty of [0, -1, 1.2, 101, NaN, Infinity, "2"]) expect(() => calculateFixedScenarioHedge([{ id: "no3", qty, price: 52, fee: 1 }])).toThrow();
    for (const price of [-1, 101, NaN, Infinity, "52"]) expect(() => calculateFixedScenarioHedge([{ id: "no3", qty: 2, price, fee: 1 }])).toThrow();
    for (const fee of [-1, 101, NaN, Infinity, "1"]) expect(() => calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 52, fee }])).toThrow();
    expect(() => calculateFixedScenarioHedge([{ id: "unknown", qty: 2, price: 52, fee: 1 }])).toThrow();
    expect(() => calculateFixedScenarioHedge(null)).toThrow();
    expect(() => calculateFixedScenarioHedge([null])).toThrow();
  });

  it("valid price boundaries and fractional cents retain signed net outcomes", () => {
    expect(calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 0, fee: 0 }]).worstCase).toBe(1.22);
    expect(calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 100, fee: 100 }]).worstCase).toBe(-2.78);
    expect(calculateFixedScenarioHedge([{ id: "no3", qty: 2, price: 52.5, fee: 1 }]).worstCase).toBe(0.15);
  });
});

describe("Hedge Finder sizing against uncovered gross payout", () => {
  const placedJalen = (): SlipLeg[] => markPlaced(buildNBASampleDraft(), SAMPLE_STAMP).legs.slice();

  it("a recorded Jalen DraftKings $10 at +145 exposes $24.50; the Kalshi NO hedge rounds up to 25 whole contracts", () => {
    const legs = placedJalen();
    const no = sampleQuote("jalen", "kalshi", "no");
    const u = uncoveredPayout(legs, no.market.id, "no");
    expect(u).toMatchObject({ ok: true, dollars: 24.5 });
    expect(sizeHedge(legs, no.market.id, "no", no.quote.price)).toEqual({ ok: true, amount: 25, unit: "contracts", grossPayout: 25 });
    const draft = { ...emptyDraft(), legs };
    const h = makeHedgeLeg(draft, no.quote, no.market);
    expect(h.ok && h.leg.amount).toBe(25);
  });

  it("a sportsbook hedge stake rounds up to the cent (DraftKings NO at -175 covering $24.50 needs $15.60)", () => {
    const legs = placedJalen();
    const no = sampleQuote("jalen", "draftkings", "no");
    expect(sizeHedge(legs, no.market.id, "no", no.quote.price)).toEqual({ ok: true, amount: 15.6, unit: "stake_dollars", grossPayout: 24.514286 });
  });

  it("same-side holdings reduce the uncovered payout; nothing left means nothing to size", () => {
    const legs = placedJalen();
    const yes = sampleQuote("jalen", "kalshi", "yes");
    expect(uncoveredPayout(legs, yes.market.id, "yes")).toMatchObject({ ok: true, dollars: 0 });
    expect(sizeHedge(legs, yes.market.id, "yes", yes.quote.price)).toMatchObject({ ok: false, code: "nothing_to_cover" });
    const no = sampleQuote("jalen", "kalshi", "no");
    const mixed = [...legs, makeLeg(no.quote, no.market, "existing", { amount: 10, placedAt: SAMPLE_STAMP })];
    expect(uncoveredPayout(mixed, no.market.id, "no")).toMatchObject({ ok: true, dollars: 14.5 });
    expect(sizeToPayout(14.5, no.quote.price)).toMatchObject({ ok: true, amount: 15 });
  });

  it("planned legs are not positions to hedge, and entry picks are never sized", () => {
    const planned = buildNBASampleDraft().legs.slice();
    const no = sampleQuote("jalen", "kalshi", "no");
    expect(uncoveredPayout(planned, no.market.id, "no")).toMatchObject({ ok: true, dollars: 0 });
    const pick = sampleQuote("jalen", "prizepicks", "no");
    expect(sizeToPayout(24.5, pick.quote.price)).toMatchObject({ ok: false, code: "entry_pick" });
    const heldPick = makeLeg(sampleQuote("jalen", "prizepicks", "yes").quote, no.market, "existing", { placedAt: SAMPLE_STAMP });
    expect(uncoveredPayout([heldPick], no.market.id, "no")).toMatchObject({ ok: false, code: "entry_in_market" });
  });
});

describe("enumeration cap", () => {
  it("enumerates every combination up to the cap and refuses above it with a typed result", () => {
    const ten = Array.from({ length: MAX_ENUMERATED_MARKETS }, (_, i) => `m${i}`);
    const ok = enumerateOutcomes(ten);
    expect(ok.ok && ok.outcomes.length).toBe(2 ** MAX_ENUMERATED_MARKETS);
    const over = enumerateOutcomes([...ten, "m-extra"]);
    expect(over).toMatchObject({ ok: false, code: "too_many_markets", count: MAX_ENUMERATED_MARKETS + 1, cap: MAX_ENUMERATED_MARKETS });
    expect(enumerateOutcomes(["a", "a", "b"])).toMatchObject({ ok: true, outcomes: { length: 4 } });
  });

  it("a slip over the cap shows no table and no coverage, and says why", () => {
    const base = sampleQuote("bobby", "kalshi", "yes");
    const legs = Array.from({ length: 4 }, (_, i) => {
      const market: Market = { ...base.market, id: `cap-m${i}`, playerId: `cap-p${i}`, eventId: `cap-e${i}` };
      const quote: Quote = { ...base.quote, id: `cap-q${i}`, marketId: market.id };
      return makeLeg(quote, market);
    });
    const r = evaluateDraft({ ...emptyDraft(), legs }, { now: Date.parse(SAMPLE_STAMP), maxMarkets: 3 });
    expect(r.rows).toEqual([]);
    expect(r.coverage).toMatchObject({ shown: false, reason: "too_many_markets" });
    expect(r.warnings.some((w) => w.includes("the limit is 3 markets"))).toBe(true);
    expect(evaluateDraft({ ...emptyDraft(), legs }, { now: Date.parse(SAMPLE_STAMP) }).rows.length).toBe(16);
  });
});
