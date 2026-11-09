import { describe, expect, it } from "vitest";
import type { EdgeContract } from "./edges";
import {
  buckets, candidatesFor, centsText, clampLeg, legCost, legFromContract, legWins, outcomes, summarize, totalCost, type HedgeLeg,
} from "./hedge";

const G = { home: "PHX", away: "ORL" };
const leg = (o: Partial<HedgeLeg>): HedgeLeg => ({ id: "x", kind: "stat", side: "yes", label: "l", threshold: 3, contracts: 2, ask_c: 39, fee_c: 1, source: "entered", ...o });
const row = (o: Partial<EdgeContract>): EdgeContract => ({ side: "over", threshold: 3, title: "", ticker: "k", price: 0.39, fees: 0.01, ev: -0.01, ev_low: null, model_prob: 0.4, prob_lo: null, prob_hi: null, order_type: "", price_ts: "", ...o });

describe("position lab arithmetic", () => {
  it("costs contracts x (ask + fee), in cents", () => {
    expect(legCost(leg({}))).toBe(80);
    expect(totalCost([leg({}), leg({ side: "no", ask_c: 52, fee_c: 1 })])).toBe(80 + 106);
  });
  it("a YES and its exact NO complement pay in every outcome: the net is 100 per contract pair minus the cost", () => {
    const legs = [leg({}), leg({ side: "no", ask_c: 52, fee_c: 1 })];
    const os = outcomes(legs, G);
    expect(os).toHaveLength(2);
    for (const o of os) {
      expect(o.payout_c).toBe(200);
      expect(o.net_c).toBe(200 - 186);
    }
    const s = summarize(legs, G);
    expect(s?.worst_c).toBe(14);
    expect(s?.positive_everywhere).toBe(true);
  });
  it("a lone YES loses its whole cost below the line and pays above it", () => {
    const os = outcomes([leg({})], G);
    expect(os.map((o) => o.net_c)).toEqual([-80, 120]);
    expect(summarize([leg({})], G)).toMatchObject({ worst_c: -80, best_c: 120, positive_everywhere: false });
  });
  it("cuts buckets at every threshold", () => {
    expect(buckets([leg({ threshold: 2 }), leg({ threshold: 4, side: "no" })]).map((b) => b.label)).toEqual(["0-1", "2-3", "4+"]);
    expect(buckets([leg({ threshold: 1 })]).map((b) => b.label)).toEqual(["0", "1+"]);
    expect(buckets([leg({ kind: "team", team: "home", threshold: undefined })]).map((b) => b.label)).toEqual(["any count"]);
  });
  it("crosses the stat with the game result when a team leg is in play", () => {
    const team = leg({ kind: "team", team: "home", threshold: undefined, ask_c: 60, fee_c: 1, contracts: 1 });
    const os = outcomes([leg({}), team], G);
    expect(os).toHaveLength(4);
    expect(os.find((o) => o.id === "3+|home")?.paying).toBe(2);
    expect(legWins(team, 0, "away")).toBe(false);
    expect(legWins({ ...team, side: "no" }, 0, "away")).toBe(true);
  });
  it("leaves a leg out until it has contracts and an ask (a placeholder is never priced for you)", () => {
    const placeholder = leg({ kind: "team", team: "away", threshold: undefined, contracts: 0, ask_c: 0 });
    expect(outcomes([leg({}), placeholder], G)).toHaveLength(2);
    expect(summarize([placeholder], G)).toBeNull();
  });
  it("keeps entered numbers in range", () => {
    expect(clampLeg(leg({ contracts: 1e9, ask_c: 150, fee_c: -3 }))).toMatchObject({ contracts: 1000, ask_c: 99, fee_c: 0 });
    expect(clampLeg(leg({ contracts: Number.NaN, ask_c: Number.NaN }))).toMatchObject({ contracts: 0, ask_c: 0 });
  });
  it("takes price and fee from a stored row (dollars to cents) and says how it reads", () => {
    const l = legFromContract(row({ price: 0.81, fees: 0.02 }), "threes");
    expect(l).toMatchObject({ ask_c: 81, fee_c: 2, side: "yes", source: "stored" });
    expect(centsText(-7.5)).toBe("-7.5c");
    expect(centsText(14)).toBe("+14c");
  });
  it("offers the complement (stored, else a placeholder to price yourself), and no-price game legs", () => {
    const base = legFromContract(row({}), "threes", 1);
    const stored = candidatesFor(base, [row({}), row({ side: "under", price: 0.52 })], "threes", G);
    expect(stored[0]?.leg).toMatchObject({ side: "no", ask_c: 52, source: "stored" });
    expect(stored[0]?.note).toMatch(/Exact complement/);
    const none = candidatesFor(base, [row({})], "threes", G);
    expect(none[0]?.leg).toMatchObject({ side: "no", ask_c: 0, contracts: 0 });
    expect(none.filter((c) => c.leg.kind === "team").every((c) => c.leg.ask_c === 0)).toBe(true);
  });
});
