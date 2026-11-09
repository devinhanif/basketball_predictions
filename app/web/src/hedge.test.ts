import { describe, expect, it } from "vitest";
import type { EdgeContract } from "./edges";
import {
  legFromPlay, legWords, buckets, candidatesFor, centsText, clampLeg, legCost, legFromContract, legWins, outcomes, summarize, totalCost, type HedgeLeg,
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

describe("sides and wording (the lab's payoff direction matches the play)", () => {
  const play = (side: string, threshold: number, leg_desc: string | null) => ({ side, threshold, price_cents: 38, fee_cents: 2, leg_desc, player_name: "Austin Reaves" });
  it("says the side once: YES is 'or more', NO is 'fewer than'", () => {
    expect(legWords("yes", 6, "assists")).toBe("6 or more assists");
    expect(legWords("no", 6, "assists")).toBe("fewer than 6 assists");
    expect(legFromContract(row({ side: "under", threshold: 6 }), "assists", 1, "A B").label).toBe("A B: fewer than 6 assists");
  });
  it("uses the play's own wording as the label, never a second NO/YES prefix", () => {
    const l = legFromPlay(play("no", 6, "Austin Reaves: fewer than 6 assists"), "assists", 2, "stored");
    expect(l?.label).toBe("Austin Reaves: fewer than 6 assists");
    expect(legFromPlay(play("yes", 6, null), "assists", null, "entered")?.label).toBe("Austin Reaves: 6 or more assists");
    expect(legFromPlay({ ...play("yes", 6, null), threshold: null }, "assists", null, "entered")).toBeNull();
  });
  it("pays in the direction of the side and threshold: NO on 6 ('fewer than 6' / under 5.5) wins at 5, loses at 6", () => {
    const no6 = legFromPlay(play("no", 6, "x"), "assists", 2, "stored") as HedgeLeg;
    expect([no6.side, legWins(no6, 5, null), legWins(no6, 6, null), legWins(no6, 9, null)]).toEqual(["no", true, false, false]);
    const yes6 = legFromPlay(play("yes", 6, "x"), "assists", 2, "stored") as HedgeLeg;
    expect([yes6.side, legWins(yes6, 5, null), legWins(yes6, 6, null), legWins(yes6, 9, null)]).toEqual(["yes", false, true, true]);
    // over 5.5 = YES 6+, under 5.5 = NO 6+ : the same two legs, from the export's rows
    const over = legFromContract(row({ side: "over", threshold: 6 }), "assists");
    const under = legFromContract(row({ side: "under", threshold: 6 }), "assists");
    expect([legWins(over, 6, null), legWins(over, 5, null), legWins(under, 5, null), legWins(under, 6, null)]).toEqual([true, false, true, false]);
  });
  it("the grid of a lone NO 6+ at 38c + 2c: +60 below the line, -40 at or above it", () => {
    const no6 = legFromPlay(play("no", 6, "x"), "assists", 2, "stored") as HedgeLeg;
    expect(outcomes([no6], G).map((o) => [o.label, o.net_c])).toEqual([["0-5", 60], ["6+", -40]]);
    const s = summarize([no6], G);
    expect([s?.worst_c, s?.best_c]).toEqual([-40, 60]);
  });
});
