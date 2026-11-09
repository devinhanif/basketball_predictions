import { beforeEach, describe, expect, it } from "vitest";
import { buildNotices, categoryOfKind, filterNotices, markRead, unreadCount, NOTIFY_DEFAULTS } from "./alerts";
import {
  barScale, calibration, clvRate, correctionLinks, dayBars, dayTally, filterLedger, ledgerCsv, parseDesk, settledPlays, signedCents, winRate,
  type Desk, type DeskObservation, type DeskPlay,
} from "./desk";
import { canSeeAdmin, cleanName, initials, type Identity } from "./identity";
import { radarRows, edgeSentence } from "./discover";
import { moveSelection, search, type SearchItem } from "./search";
import { cleanItem, cleanWatchDoc, MAX_WATCH, mergeWatch, resetWatch, toggleItem, toggleWatch, getWatch } from "./watch";

const play = (id: string, o: Partial<DeskPlay> = {}): DeskPlay => ({
  play_id: id, slate_date: "2026-11-07", ts: `2026-11-07T20:0${id}:00Z`, game_id: "0022600197", matchup: "LAL@DET", tipoff: null, kind: "prop", side: "yes", player_id: 7, player_name: "A B",
  team: null, stat: "reb", threshold: 4, leg_desc: "A B: 4+ rebounds", price_cents: 40, fee_cents: null, fair_prob: 0.4, model_prob: 0.43, edge_cents: -1, edge_label: "none", reason: null,
  contracts: null, stake_usd: null, result: { status: "settled", won: false, close_cents: 41, clv_cents: -0.5, pnl_usd: null }, ...o,
});
const obs = (id: string, o: Partial<DeskObservation> = {}): DeskObservation => ({
  id, slate_date: "2026-11-07", ts: "2026-11-07T20:00:00Z", game_id: "g", matchup: "A@B", when_text: null, kind: "foul", title: `t-${id}`, text: null, certainty: "reported", corrects: null, ...o,
});
function desk(o: Partial<Desk> = {}): Desk {
  const d = parseDesk({
    schema: 1, privacy: "shared", built_at: "2026-11-08T00:00:00Z", data_as_of: "2026-11-08T00:00:00Z",
    status: { kill_switch: { on: true, since: "2026-11-03T13:01:34Z", n: 30, mean_clv_cents: -1.28, note: "n" }, model_review: true, w_model: 0 },
    plays: [], observations: [], alerts: [], halftime: [], daily: [],
  }) as Desk;
  return { ...d, ...o };
}

describe("desk export", () => {
  it("accepts schema 1 only and tolerates missing optional parts", () => {
    expect(parseDesk(null)).toBeNull();
    expect(parseDesk({ schema: 2 })).toBeNull();
    expect(parseDesk({ schema: 1, privacy: "x" })).toBeNull();
    const d = desk();
    expect(d.plays).toEqual([]);
    expect(d.section_notes).toEqual({});
    expect(d.balance).toBeNull();
  });
  it("rates carry n and an interval, or say they are not enough to say", () => {
    expect(winRate({ n_settled: 128, n_won: 36, n_lost: 92, n_void: 2 }).ci).not.toBeNull();
    const few = winRate({ n_settled: 5, n_won: 2, n_lost: 3, n_void: 0 });
    expect(few.ci).toBeNull();
    expect(few.reason).toMatch(/not enough to say/);
    expect(winRate(null).reason).toMatch(/No record/);
    expect(clvRate(desk({ clv_summary: { n: 83, mean_cents: -0.88, ci95: [-1.43, -0.52], clustered_on: "slate_date", reason: null } })).ci).toEqual([-1.43, -0.52]);
    expect(clvRate(desk({ clv_summary: { n: 3, mean_cents: null, ci95: null, clustered_on: "slate_date", reason: "too few" } })).reason).toBe("too few");
    expect(clvRate(desk({ clv_summary: null })).n).toBe(0);
    expect(signedCents(null)).toBe("not stored");
  });
  it("calibration renders nothing (the honest building state) when there are no bins", () => {
    expect(calibration(desk())).toBeNull();
    const bins = [{ bin_lo: 0, bin_hi: 0.5, n: 10, mean_pred: 0.3, hit_rate: 0.2 }, { bin_lo: 0.5, bin_hi: 1, n: 0, mean_pred: 0.7, hit_rate: 0.7 }];
    const c = calibration(desk({ track_record: { scorecard_date: null, model_vs_market: null, calibration: bins } }));
    expect(c?.bins).toHaveLength(1);
    expect(c?.total).toBe(10);
  });
  it("filters the ledger by closing-line value in the shared cut and by P&L where it is stored", () => {
    const rows = [play("1", { result: { status: "settled", won: true, close_cents: 1, clv_cents: 1.5, pnl_usd: null } }), play("2"), play("3", { result: { status: "settled", won: false, close_cents: 1, clv_cents: 0, pnl_usd: null } }), play("4", { result: null })];
    expect(filterLedger(rows, "all")).toHaveLength(4);
    expect(filterLedger(rows, "positive").map((p) => p.play_id)).toEqual(["1"]);
    expect(filterLedger(rows, "negative").map((p) => p.play_id)).toEqual(["2"]);
    expect(filterLedger(rows, "flat").map((p) => p.play_id)).toEqual(["3"]);
    const priv = [play("5", { result: { status: "settled", won: true, close_cents: 1, clv_cents: -2, pnl_usd: 0.5 } })];
    expect(filterLedger(priv, "positive")).toHaveLength(1);
    expect(settledPlays(desk({ plays: rows }))).toHaveLength(3);
  });
  it("exports the ledger as CSV, with money columns only when the cut holds them", () => {
    const csv = ledgerCsv([play("1", { leg_desc: 'A "B", 4+' })], false);
    expect(csv.split("\n")[0]).toBe("slate_date,matchup,contract,side,entry_cents,fee_cents,edge_cents,close_cents,clv_cents,won");
    expect(csv).toContain('"A ""B"", 4+"');
    expect(csv).not.toContain("pnl");
    expect(ledgerCsv([play("1")], true).split("\n")[0]).toMatch(/pnl_usd$/);
  });
  it("day bars: sorted, scaled, tallied with signs", () => {
    const d = desk({ daily: [
      { slate_date: "2026-11-02", n: 2, n_won: 1, clv_mean_cents: -0.5, pnl_usd: null },
      { slate_date: "2026-11-01", n: 3, n_won: 1, clv_mean_cents: 1.2, pnl_usd: null },
      { slate_date: "2026-11-03", n: 1, n_won: 0, clv_mean_cents: null, pnl_usd: null },
    ] });
    const bars = dayBars(d);
    expect(bars.map((b) => b.date)).toEqual(["2026-11-01", "2026-11-02", "2026-11-03"]);
    expect(barScale(bars)).toBe(1.2);
    expect(dayTally(bars)).toEqual({ up: 1, down: 1, flat: 0, none: 1 });
    expect(barScale([])).toBe(0.5);
  });
  it("links corrections written as flag keys to the earlier observation of that game and athlete", () => {
    const orig = obs("h1:401:55", { ts: "2026-11-07T20:00:00Z", kind: "rotation", certainty: "inference", title: "may be done" });
    const corr = obs("h2:401:55", { ts: "2026-11-07T21:00:00Z", kind: "correction", certainty: "correction", corrects: "flag:2026-11-07:401:55", title: "returned" });
    const direct = obs("c3", { certainty: "correction", corrects: "h1:401:55", ts: "2026-11-07T22:00:00Z" });
    const links = correctionLinks([orig, corr, direct]);
    expect(links.target(corr)?.id).toBe("h1:401:55");
    expect(links.correctedBy.get("h1:401:55")?.id).toBe("c3"); // the newest correction wins
    expect(links.target(orig)).toBeNull();
  });
});

describe("notices and read state", () => {
  const d = desk({
    observations: [obs("o1", { kind: "foul", title: "Foul trouble", ts: "2026-11-07T20:00:00Z" }), obs("o2", { kind: "rotation", certainty: "inference", ts: "2026-11-07T21:00:00Z" }), obs("o3", { kind: "correction", certainty: "correction", ts: "2026-11-07T22:00:00Z" }), obs("o4", { kind: "risk", ts: "2026-11-05T00:00:00Z" })],
    alerts: [
      { id: "a1", ts: "2026-11-07T20:01:00Z", slate_date: "2026-11-07", kind: "ingame_foul", mode: "sent", title: "Foul alert", text: null, play_ids: ["1"], observation_ids: ["o1"] },
      { id: "a2", ts: "2026-11-07T23:00:00Z", slate_date: "2026-11-07", kind: "slate", mode: null, title: "Slate post", text: null, play_ids: [], observation_ids: [] },
    ],
    plays: [play("1")],
  });
  it("maps stored kinds to filter categories", () => {
    expect(categoryOfKind("ingame_foul")).toBe("foul");
    expect(categoryOfKind("ingame_benched")).toBe("rotation");
    expect(categoryOfKind("ingame_returned")).toBe("rotation");
    expect(categoryOfKind("kill_switch")).toBe("risk");
    expect(categoryOfKind("correction")).toBe("correction");
    expect(categoryOfKind("slate")).toBe("other");
  });
  it("shows an observation through the alert that links it, never twice, and never invents alert text", () => {
    const list = buildNotices(d);
    expect(list[0]?.source).toBe("status"); // the kill switch leads, with its own population named
    expect(list[0]?.text).toMatch(/n = ?30|across 30/);
    expect(list.some((n) => n.id === "obs:o1")).toBe(false);
    expect(list.some((n) => n.id === "obs:o4")).toBe(false); // the risk observation is the status notice
    const a1 = list.find((n) => n.id === "alert:a1");
    expect(a1?.text).toBeNull();
    expect(a1?.fallback).toMatch(/message text was not stored/);
    expect(a1?.fallback).toContain("Foul trouble");
    expect(a1?.matchup).toBe("A@B");
  });
  it("filters and counts unread; mark read is a copy and capped", () => {
    const list = buildNotices(d);
    const read = new Set([list[1]?.id ?? ""]);
    expect(filterNotices(list, "all", read)).toHaveLength(list.length);
    expect(filterNotices(list, "unread", read)).toHaveLength(list.length - 1);
    expect(filterNotices(list, "foul", read).every((n) => n.category === "foul")).toBe(true);
    expect(filterNotices(list, "risk", read)[0]?.source).toBe("status");
    expect(filterNotices(list, "correction", read).map((n) => n.id)).toEqual(["obs:o3"]);
    expect(unreadCount(list, read, NOTIFY_DEFAULTS)).toBe(list.length - 1);
    expect(unreadCount(list, new Set(), { fouls: false, risk: false, corrections: false })).toBe(list.filter((n) => n.category === "other").length);
    const next = markRead(read, ["x", "y"]);
    expect(read.size).toBe(1);
    expect(next.size).toBe(3);
    expect(markRead(new Set(["a", "b", "c"]), ["d"], 2).size).toBe(2);
  });
});

describe("watchlist store", () => {
  beforeEach(() => { resetWatch(); });
  it("validates items from other viewers and caps the document", () => {
    expect(cleanItem({ key: "k", label: "L", sub: "", at: "2026-11-07T20:00:00Z" })).not.toBeNull();
    expect(cleanItem({ key: "k", label: "x".repeat(200), sub: "", at: "2026-11-07T20:00:00Z" })).toBeNull();
    expect(cleanItem({ key: "k", label: "L", sub: "", at: "nope" })).toBeNull();
    expect(cleanItem(5)).toBeNull();
    const items = Array.from({ length: 150 }, (_, i) => ({ key: `k${i.toString()}`, label: "L", sub: "", at: "2026-11-07T20:00:00Z" }));
    expect(cleanWatchDoc({ v: 1, items })).toHaveLength(MAX_WATCH);
    expect(cleanWatchDoc({ v: 2, items })).toEqual([]);
  });
  it("toggles, merges (earlier time wins) and works with no db at all", () => {
    const a = { key: "a", label: "A", sub: "", at: "2026-11-07T20:00:00Z" };
    expect(toggleItem([], a)).toEqual([a]);
    expect(toggleItem([a], a)).toEqual([]);
    expect(mergeWatch([{ ...a, at: "2026-11-07T21:00:00Z" }], [a])[0]?.at).toBe("2026-11-07T20:00:00Z");
    toggleWatch({ key: "z", label: "Z", sub: "" }, new Date("2026-11-08T00:00:00Z"));
    expect(getWatch().mine.map((i) => i.key)).toEqual(["z"]);
    toggleWatch({ key: "z", label: "Z", sub: "" });
    expect(getWatch().mine).toEqual([]);
  });
});

describe("admin gating (never a password)", () => {
  const id = (o: Partial<Identity>): Identity => ({ source: "platform", id: "u", name: "n", avatarUrl: null, isOwner: false, canEdit: false, loading: false, ...o });
  it("outside the artifact the private build decides; there is no other input", () => {
    expect(canSeeAdmin(id({}), false)).toBe(false);
    expect(canSeeAdmin(id({ isOwner: true }), false)).toBe(false);
    expect(canSeeAdmin(id({}), true)).toBe(true);
  });
  it("cleans names and makes initials", () => {
    expect(cleanName("  Dev   in  ")).toBe("Dev in");
    expect(cleanName("x".repeat(99))).toHaveLength(40);
    expect(initials("Giannis Antetokounmpo")).toBe("GA");
    expect(initials("")).toBe("?");
  });
});

describe("Discover rows and search", () => {
  it("prefers the desk's plays for the night, best edge first, else the export's contracts for that night", () => {
    const d = desk({ plays: [play("1", { edge_cents: -1 }), play("2", { edge_cents: 2.5, edge_label: "thin" })] });
    const rows = radarRows(d, "2026-11-07", null);
    expect(rows.map((r) => r.key)).toEqual(["desk:2", "desk:1"]);
    expect(rows[0]?.status).toMatch(/Thin edge/);
    expect(radarRows(d, "2026-11-09", null)).toEqual([]);
    const c = { side: "over" as const, threshold: 3, title: "", ticker: "T", price: 0.81, fees: 0.02, ev: -0.075, ev_low: null, model_prob: 0.755, prob_lo: null, prob_hi: null, price_ts: "", game_id: "g", player_id: 1, player: "P", team: "AAA", stat: "fg3m", matchup: "AAA @ BBB", tip_utc: null };
    const ex = radarRows(null, "2026-11-08", { report_date: "2026-11-08", contracts: [c] });
    expect(ex[0]?.status).toBe("Negative after fees");
    expect(edgeSentence(ex[0] as NonNullable<(typeof ex)[0]>)).toBe("Model 75.5% vs ask 81c + fee 2c = edge -7.5c per $1");
    const noFee = radarRows(desk({ plays: [play("9", { fee_cents: null, edge_cents: 11.4, model_prob: 0.665, price_cents: 38 })] }), "2026-11-07", null)[0];
    expect(edgeSentence(noFee as NonNullable<typeof noFee>)).toContain("fee is not in the shared cut");
    expect(radarRows(desk({ plays: [play("8", { player_name: null, player_id: null, leg_desc: "BKN to win", stat: null, threshold: null })] }), "2026-11-07", null)[0]?.name).toBe("BKN to win");
    expect(radarRows(null, "2026-11-07", { report_date: "2026-11-08", contracts: [c] })).toEqual([]);
  });
  const idx: SearchItem[] = [
    { kind: "screen", label: "Alerts", sub: "Notification center", href: "#/alerts" },
    { kind: "game", label: "PHI at CHI", sub: "2026-11-08", href: "#/game/1", words: "Philadelphia 76ers Chicago Bulls" },
    { kind: "player", label: "Luka Dončić", sub: "LAL", href: "#/leg/1/2/pts" },
  ];
  it("searches screens, games and players; ignores accents; every word must match", () => {
    expect(search(idx, "").map((i) => i.kind)).toEqual(["screen"]);
    expect(search(idx, "doncic")[0]?.label).toBe("Luka Dončić");
    expect(search(idx, "76ers bulls")[0]?.kind).toBe("game");
    expect(search(idx, "alerts luka")).toEqual([]);
    expect(search(idx, "zzz")).toEqual([]);
  });
  it("moves the selection with wrap-around", () => {
    expect(moveSelection(0, -1, 3)).toBe(2);
    expect(moveSelection(2, 1, 3)).toBe(0);
    expect(moveSelection(0, 1, 0)).toBe(0);
  });
});
