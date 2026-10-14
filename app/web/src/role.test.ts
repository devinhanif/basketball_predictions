import { describe, expect, it } from "vitest";
import { badgeText, carriedText, extraText, gapText, netText, onoffN, pastText, per36N, per36Text, possText, signed, thousands } from "./role";
import type { BeyondStat, OnOffWindow, Per36, Rate, Steadiness } from "./roleTypes";

const rate = (net: number | null, poss = 1420): Rate => ({ poss, off_poss: poss / 2, def_poss: poss / 2, ortg: null, drtg: null, net });
const win = (over: Partial<OnOffWindow> = {}): OnOffWindow => ({
  games: 12,
  on: rate(11),
  off: rate(7.6),
  diff: 3.4,
  ci_lo: -4.5,
  ci_hi: 11.2,
  small: false,
  note: "",
  ...over,
});
const steady = (label: Steadiness["label"], n: number): Steadiness => ({
  label,
  n,
  window: 15,
  avg_minutes: 30,
  sd_minutes: 4,
  cv: 0.13,
  say: "",
  caveat: "",
});

describe("role formatting", () => {
  it("signs gaps with a real minus and never prints -0.0", () => {
    expect(signed(3.44)).toBe("+3.4");
    expect(signed(-4.66)).toBe("-4.7");
    expect(signed(-0.02)).toBe("0.0");
    expect(signed(0)).toBe("0.0");
  });

  it("prints thousands and possessions", () => {
    expect(thousands(15284)).toBe("15,284");
    expect(possText(rate(1, 1420))).toBe("1,420 possessions");
  });

  it("shows a missing side as none, not zero", () => {
    expect(netText(rate(null))).toBe("none");
    expect(netText(rate(-12.5))).toBe("-12.5");
  });

  it("gap text carries the interval when there is one", () => {
    expect(gapText(win())).toBe("gap +3.4, 95% range -4.5 to +11.2");
    expect(gapText(win({ ci_lo: null, ci_hi: null }))).toBe("gap +3.4");
    expect(gapText(win({ diff: null }))).toBeNull();
  });

  it("flags small samples beside the game count", () => {
    expect(onoffN(win())).toBe("12 games");
    expect(onoffN(win({ games: 1, small: true }))).toBe("1 game, small sample");
  });

  it("per-36 text and n", () => {
    const p: Per36 = { n: 4, avg_minutes: 30, pts36: 12, reb36: 6, ast36: 2.4, small: true };
    expect(per36Text(p)).toBe("12.0 pts, 6.0 reb, 2.4 ast per 36");
    expect(per36N(p)).toBe("n = 4, small sample");
    expect(per36Text({ ...p, n: 0, pts36: null, reb36: null, ast36: null })).toBeNull();
    expect(per36N({ ...p, n: 11, small: false })).toBe("n = 11");
  });

  it("the badge never appears without its n", () => {
    expect(badgeText(steady("Steady", 15))).toBe("Steady, last 15 games");
    expect(badgeText(steady("Mixed", 12))).toBe("Mixed, last 12 games");
    expect(badgeText(steady("Limited data", 3))).toBe("Limited data, 3 games");
    expect(badgeText(steady("Limited data", 1))).toBe("Limited data, 1 game");
  });
});

const beyondStat = (over: Partial<BeyondStat> = {}): BeyondStat => ({
  stat: "ast",
  label: "Assists",
  past_gap: 4,
  expected_extra: 0.138,
  no_effect: false,
  one_in: 21,
  large_gap: false,
  window: "2020-22",
  ...over,
});

describe("shrunk with/without headline", () => {
  it("leads with the small shrunk number to two decimals", () => {
    expect(extraText(beyondStat())).toBe("+0.14");
    expect(extraText(beyondStat({ expected_extra: -0.1476 }))).toBe("-0.15");
  });

  it("threes say no measurable effect, never a number", () => {
    const t = beyondStat({ stat: "fg3m", label: "Threes", no_effect: true, expected_extra: 0, one_in: null });
    expect(extraText(t)).toBe("no measurable effect");
    expect(carriedText(t)).toBe("");
  });

  it("the raw history stays, labelled past games and carrying its n, and is not the headline", () => {
    const s = beyondStat();
    expect(pastText(s, 15)).toBe("past games +4.0 a game, n = 15");
    expect(extraText(s)).not.toContain("past games");
    expect(extraText(s)).not.toBe(signed(s.past_gap ?? 0));
    expect(carriedText(s)).toBe("about 1 in 21 carried over");
  });

  it("no gap means no number, said in words", () => {
    const s = beyondStat({ past_gap: null, expected_extra: null });
    expect(extraText(s)).toBe("none");
    expect(pastText(s, 0)).toBe("no games to compare");
  });
});
