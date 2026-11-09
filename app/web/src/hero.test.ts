import { describe, expect, it } from "vitest";
import { clusteredRate, isSingle, parseDesk, type Desk, type DeskPlay } from "./desk";
import { fairAgeMin, heroFootnote, pickHero, radarRows } from "./discover";

const REASON_STALE = "ask 38.0c (6 min old) + fee 2.0c vs Pinnacle no-vig 51.4c (o5.5, 238 min old): +11.4c per $1";
const REASON_FRESH = "ask 40.0c (3 min old) + fee 2.0c vs Pinnacle no-vig 44.0c (o3.5, 12 min old): +2.0c per $1";

const play = (id: string, o: Partial<DeskPlay> = {}): DeskPlay => ({
  play_id: id, slate_date: "2026-11-08", ts: "2026-11-08T17:00:00Z", game_id: "g", matchup: "LAL@DET", tipoff: null, kind: "prop", side: "no", player_id: 7, player_name: "A B", team: null,
  stat: "ast", threshold: 6, leg_desc: "A B: fewer than 6 assists", price_cents: 38, fee_cents: null, fair_prob: 0.514, model_prob: 0.66, edge_cents: 11.4, edge_label: "thin", reason: REASON_STALE,
  contracts: null, stake_usd: null, result: null, ...o,
});
function desk(plays: DeskPlay[]): Desk {
  return parseDesk({
    schema: 1, privacy: "shared", built_at: "x", data_as_of: "x", plays, record: { n_settled: 130, n_won: 36, n_lost: 92, n_void: 2 },
    status: { kill_switch: { on: true, since: "2026-11-03T13:00:00Z", n: 30, mean_clv_cents: -1.28, note: null }, model_review: true, w_model: 0 },
  }) as Desk;
}

describe("the hero is never a stale number dressed as a bet", () => {
  it("reads the fair price's age only from what the stored note says", () => {
    expect(fairAgeMin(REASON_STALE)).toBe(238);
    expect(fairAgeMin(REASON_FRESH)).toBe(12);
    expect(fairAgeMin("no ages stated")).toBeNull();
    expect(fairAgeMin(null)).toBeNull();
  });
  it("marks a row stale past 60 minutes and says so in its status", () => {
    const rows = radarRows(desk([play("1")]), "2026-11-08", null);
    expect(rows[0]?.stale).toBe(true);
    expect(rows[0]?.fairAge).toBe(238);
    expect(rows[0]?.status).toMatch(/238 min old \(stale\)/);
    expect(rows[0]?.reason).toBe(REASON_STALE);
  });
  it("prefers the best fresh play over a better but stale one; falls back to the stale best", () => {
    const d = desk([play("1"), play("2", { edge_cents: 2, reason: REASON_FRESH, fair_prob: 0.44 })]);
    const rows = radarRows(d, "2026-11-08", null);
    expect(rows[0]?.key).toBe("desk:1"); // best edge first in the radar
    expect(pickHero(rows)?.key).toBe("desk:2"); // but the hero is the fresh one
    const onlyStale = radarRows(desk([play("1")]), "2026-11-08", null);
    expect(pickHero(onlyStale)?.key).toBe("desk:1");
    expect(pickHero([])).toBeNull();
  });
  it("a desk row's footnote states the kill switch and keep your money, never 'the model has no track record'", () => {
    const d = desk([play("1", { edge_cents: -1 }), play("3", { reason: REASON_FRESH, edge_cents: 2 })]);
    const rows = radarRows(d, "2026-11-08", null);
    for (const r of rows) {
      const t = heroFootnote(r, d);
      expect(t).toContain("JARVIS's kill switch is on (36-92 on 128 settled) · keep your money");
      expect(t).not.toMatch(/no track record/i);
    }
  });
  it("an engine row's footnote is worded for the engine", () => {
    const c = { side: "over" as const, threshold: 3, title: "", ticker: "T", price: 0.5, fees: 0.01, ev: 0.02, ev_low: null, model_prob: 0.55, prob_lo: null, prob_hi: null, price_ts: "", game_id: "g", player_id: 1, player: "P", team: "AAA", stat: "fg3m", matchup: "AAA @ BBB", tip_utc: null };
    const r = radarRows(null, "2026-11-08", { report_date: "2026-11-08", contracts: [c] })[0];
    expect(heroFootnote(r as NonNullable<typeof r>, null)).toMatch(/parlay engine.*no track record yet/);
  });
});

describe("record rate, clustered on date", () => {
  const settled = (id: string, date: string, won: boolean, label = "thin"): DeskPlay =>
    play(id, { slate_date: date, edge_label: label, result: { status: "settled", won, close_cents: 1, clv_cents: 0, pnl_usd: null } });
  const dates = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"];
  const plays = dates.flatMap((d, i) => [settled(`a${i.toString()}`, d, i % 3 === 0), settled(`b${i.toString()}`, d, false), settled(`c${i.toString()}`, d, false, "daily_dollar")]);
  it("leaves daily-dollar legs out of the rate and counts them", () => {
    expect(isSingle(plays[2] as DeskPlay)).toBe(false);
    const r = clusteredRate(plays);
    expect(r.n).toBe(12);
    expect(r.excluded).toBe(6);
    expect(r.won).toBe(2);
    expect(r.nDates).toBe(6);
  });
  it("resamples whole dates, reproducibly, and the interval holds the rate", () => {
    const a = clusteredRate(plays);
    const b = clusteredRate(plays);
    expect(a.ci).toEqual(b.ci);
    expect(a.seed).toBe(20261108);
    expect(a.reps).toBe(2000);
    expect(a.ci?.[0]).toBeLessThanOrEqual(a.rate ?? 0);
    expect(a.ci?.[1]).toBeGreaterThanOrEqual(a.rate ?? 1);
  });
  it("says not enough to say with too few dates or no bets", () => {
    const few = clusteredRate(plays.filter((p) => p.slate_date <= "2026-11-03"));
    expect(few.ci).toBeNull();
    expect(few.reason).toMatch(/not enough to say/);
    expect(clusteredRate([]).reason).toMatch(/No decided single bets/);
  });
});
