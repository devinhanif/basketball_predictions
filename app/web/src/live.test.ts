import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ATTRIBUTION,
  CHECKPOINT_LABEL,
  STALE_AFTER_S,
  ageNow,
  ageText,
  applyDelta,
  buildTrackers,
  etDate,
  expectedBy,
  findLive,
  followsOn,
  fromSnapshot,
  matchRow,
  mergePlays,
  needed,
  normName,
  paceOf,
  previousDate,
  chanceLocked,
  treatAsFinal,
  type BoxRow,
  type Delta,
  type LiveGameSummary,
  type LiveMeta,
  type Play,
  type Snapshot,
} from "./live";
import { BACKOFF_MS, CLOSED, connectLive, type LiveConn, type SourceLike, type StreamEnv } from "./useLive";
import type { PropView } from "./types";

const meta = (age: number): LiveMeta => ({ source: "capture", fetched_at: "2026-11-10T01:40:00+00:00", last_play_at: null, play_lag_s: 30, server_now: "x", age_s: age });
const play = (n: number, t = `p${n.toString()}`): Play => ({ n, p: 1, c: "10:00", t, h: 0, a: 0, tm: null, s: 0, k: "" });
const row = (id: string, name: string, team: string, over: Partial<BoxRow> = {}): BoxRow => ({
  id, name, team, starter: true, min: 20, pts: 10, reb: 4, ast: 3, fg3m: 1, stl: 0, blk: 0, tov: 1, pf: 2, fg: "4-9", fg3: "1-3", ft: "1-2", pm: "+2", ...over,
});
const snap = (): Snapshot => ({
  seq: 3,
  game_id: "401909952",
  date: "2026-11-09",
  state: "in",
  tip_utc: null,
  away: { abbr: "LAL", name: "Lakers", score: 50 },
  home: { abbr: "ATL", name: "Hawks", score: 48 },
  status: { period: 2, clock: "6:00", detail: "6:00 - 2nd", elapsed_min: 18 },
  box: { away: [row("1", "Luka Dončić", "LAL", { pts: 20 })], home: [row("2", "Trae Young", "ATL")] },
  plays: [play(2), play(1)],
  n_plays: 40,
  first_n: 1,
  meta: meta(5),
  attribution: ATTRIBUTION,
  delay_note: "",
});
const delta = (over: Partial<Delta>): Delta => ({ seq: 4, game_id: "401909952", state: "in", meta: meta(1), changed: true, ...over });

describe("the delta reducer", () => {
  it("a snapshot is sorted ascending and its box is keyed by player", () => {
    const s = fromSnapshot(snap());
    expect(s.plays.map((p) => p.n)).toEqual([1, 2]);
    expect(Object.keys(s.box).sort()).toEqual(["1", "2"]);
  });
  it("a delta adds only what changed and leaves the rest", () => {
    const s = fromSnapshot(snap());
    const d = delta({ plays: [play(3), play(4)], n_plays: 42, box: [row("1", "Luka Dončić", "LAL", { pts: 23 })], status: { period: 2, clock: "5:00", detail: "5:00 - 2nd", elapsed_min: 19 } });
    const n = applyDelta(s, d);
    expect(n.plays.map((p) => p.n)).toEqual([1, 2, 3, 4]);
    expect(n.box["1"]?.pts).toBe(23);
    expect(n.box["2"]).toBe(s.box["2"]); // untouched rows keep their identity
    expect(n.home).toBe(s.home);
    expect(n.meta.age_s).toBe(1);
    expect(n.nPlays).toBe(42);
  });
  it("an edited play replaces the old one, in place", () => {
    const have = [play(1), play(2, "old"), play(3)];
    const out = mergePlays(have, [play(2, "fixed")]);
    expect(out.map((p) => p.t)).toEqual(["p1", "fixed", "p3"]);
    expect(mergePlays(have, [])).toBe(have);
  });
  it("only the next delta in sequence follows; a gap asks for a new snapshot", () => {
    const s = fromSnapshot(snap());
    expect(followsOn(s, delta({ seq: 4 }))).toBe(true);
    expect(followsOn(s, delta({ seq: 6 }))).toBe(false);
    expect(followsOn(s, delta({ seq: 3 }))).toBe(false);
  });
});

describe("honest ages", () => {
  it("counts from the server's age and the time since receipt", () => {
    expect(ageNow(meta(12), 1000, 4000)).toBe(15);
    expect(ageNow(meta(0), 5000, 1000)).toBe(0);
    expect(ageText(12.4)).toBe("12 s");
    expect(ageText(125)).toBe("2 min 5 s");
    expect(STALE_AFTER_S).toBeGreaterThan(60); // one missed capture is not yet stale
  });
});

describe("the pace rule", () => {
  it("is half the line by the half", () => {
    expect(expectedBy(24.5, 24)).toBe(12.25);
    expect(expectedBy(24.5, 48)).toBe(24.5);
    expect(expectedBy(24.5, 60)).toBe(24.5);
    expect(expectedBy(24.5, null)).toBeNull();
  });
  it("needed counts to go over, not to reach", () => {
    expect(needed(24.5, 20)).toBe(5);
    expect(needed(24, 20)).toBe(5); // an over of 24 needs 25
    expect(needed(24.5, 30)).toBe(0);
  });
  it("cleared, on pace, behind, not started", () => {
    expect(paceOf(24.5, 25, 20)).toBe("cleared");
    expect(paceOf(24.5, 12, 24)).toBe("behind");
    expect(paceOf(24.5, 13, 24)).toBe("on pace");
    expect(paceOf(24.5, 0, 0)).toBe("not started");
    expect(paceOf(24.5, 0, null)).toBe("not started");
  });
});

describe("matching players", () => {
  const rows = [row("1", "Luka Dončić", "LAL"), row("2", "Jaren Jackson Jr.", "MEM"), row("3", "Jalen Williams", "OKC"), row("4", "Jaylin Williams", "OKC")];
  it("ignores accents, dots and suffixes", () => {
    expect(normName("Jaren Jackson Jr.")).toBe("jaren jackson");
    expect(matchRow("Luka Doncic", rows)?.id).toBe("1");
    expect(matchRow("Jaren Jackson", rows)?.id).toBe("2");
  });
  it("never guesses between two players", () => {
    expect(matchRow("J. Williams", rows)).toBeNull();
    expect(matchRow("Nobody Here", rows)).toBeNull();
  });
});

function prop(stat: "pts" | "reb", line: number, source: "line_cal" | "model", over: Partial<PropView> = {}): PropView {
  return {
    prop_id: "x", stat, line, line_source: "posted", over_price: -110, under_price: -110, price_book: "dk", line_snapshot_utc: null,
    forecast_mean: null, q: [], taus: [], model_p_over: 0.57, model_p_under: 0.43, model_p_push: 0, extrapolated: false,
    model_p_source: source, market_p_over: 0.5, forecast_stamp: "", model_source: "", model_label: "", model_detail: null,
    p_play: null, p_play_label: null, ...over,
  };
}

describe("prop trackers", () => {
  const live = fromSnapshot(snap());
  it("show the calibrated chance only, never a model-only number, and carry no price", () => {
    const ts = buildTrackers(
      [
        { name: "Luka Doncic", side: "away", props: { pts: prop("pts", 28.5, "line_cal"), reb: prop("reb", 8.5, "model") } },
        { name: "Trae Young", side: "home", props: { pts: prop("pts", 22.5, "line_cal", { line_source: "reference" }) } },
        { name: "Bench Guy", side: "home", props: { pts: prop("pts", 4.5, "line_cal") } },
      ],
      { ...live, date: "2026-10-01" }, // an unlocked night
    );
    expect(ts.map((t) => `${t.player}|${t.stat}`)).toEqual(["Luka Doncic|pts", "Luka Doncic|reb", "Bench Guy|pts"]);
    const luka = ts[0];
    expect(luka?.cur).toBe(20);
    expect(luka?.need).toBe(9);
    expect(luka?.chance).toBe(0.57);
    expect(luka?.pace).toBe("on pace"); // even pace at 18 min of 28.5 is 10.7
    expect(ts[1]?.chance).toBeNull(); // model-only is hidden
    expect(ts[2]?.pace).toBe("no box");
    for (const t of ts) expect(Object.keys(t).join(" ")).not.toMatch(/price|odds|edge|market/i);
  });
  it("on a locked night show progress and pace only: no chance, in progress or final", () => {
    const players = [{ name: "Luka Doncic", side: "away" as const, props: { pts: prop("pts", 28.5, "line_cal") } }];
    const locked = (state: "in" | "post", date: string) => buildTrackers(players, { ...live, state, date })[0];
    for (const state of ["in", "post"] as const) {
      const t = locked(state, "2026-11-09");
      expect(t?.chance).toBeNull();
      expect(t?.source).toBeNull();
      expect(t?.cur).toBe(20); // progress and pace still show
      expect(t?.pace).toBeDefined();
    }
    const open = locked("in", "2026-10-29");
    expect(open?.chance).toBe(0.57); // unlocked nights are unchanged
    expect(open?.source).toBe("line_cal");
    expect(chanceLocked("2026-10-30")).toBe(true);
    expect(chanceLocked("2026-10-29")).toBe(false);
  });
  it("carries the real calibration source, rung or line", () => {
    const players = [{ name: "Luka Doncic", side: "away" as const, props: { pts: prop("pts", 28.5, "line_cal"), reb: { ...prop("reb", 8.5, "line_cal"), model_p_source: "rung_cal" as const } } }];
    const ts = buildTrackers(players, { ...live, date: "2026-10-01" });
    expect(ts.map((t) => t.source)).toEqual(["line_cal", "rung_cal"]);
  });
  it("treats a game as over once tip was more than 4 hours ago, final body or not", () => {
    const tip = "2026-11-10T00:00:00Z";
    const at = (h: number): number => Date.parse(tip) + h * 3600_000;
    expect(treatAsFinal("post", tip, at(0))).toBe(true);
    expect(treatAsFinal("in", tip, at(3.9))).toBe(false);
    expect(treatAsFinal("in", tip, at(4.1))).toBe(true);
    expect(treatAsFinal("pre", tip, at(9))).toBe(false);
    expect(treatAsFinal("in", null, at(9))).toBe(false);
  });
});

describe("slate helpers", () => {
  it("find the live game by teams; dates are Eastern", () => {
    const g = { game_id: "1", home: { abbr: "ATL" }, away: { abbr: "LAL" } } as unknown as LiveGameSummary;
    expect(findLive([g], "ATL", "LAL")).toBe(g);
    expect(findLive([g], "LAL", "ATL")).toBeNull();
    expect(etDate(new Date("2026-11-10T03:00:00Z"))).toBe("2026-11-09");
    expect(previousDate("2026-03-01")).toBe("2026-02-28");
  });
});

// ------------------------------------------------------------------------------------------ the stream

class FakeSource implements SourceLike {
  readyState = 1;
  onerror: (() => void) | null = null;
  closed = false;
  private fns = new Map<string, (e: { data: string }) => void>();
  addEventListener(t: string, fn: (e: { data: string }) => void): void {
    this.fns.set(t, fn);
  }
  close(): void {
    this.closed = true;
    this.readyState = CLOSED;
  }
  emit(t: string, data: unknown): void {
    this.fns.get(t)?.({ data: JSON.stringify(data) });
  }
}

function harness(hidden = false) {
  const sources: FakeSource[] = [];
  const timers: { fn: () => void; ms: number; id: number }[] = [];
  let vis: (() => void) | null = null;
  const st = { hidden };
  const env: StreamEnv = {
    open: () => {
      const s = new FakeSource();
      sources.push(s);
      return s;
    },
    hidden: () => st.hidden,
    onVisibility: (fn) => {
      vis = fn;
      return () => {
        vis = null;
      };
    },
    setTimer: (fn, ms) => {
      timers.push({ fn, ms, id: timers.length + 1 });
      return timers.length;
    },
    clearTimer: () => undefined,
    now: () => 0,
    random: () => 0,
  };
  const seen: LiveConn[] = [];
  const stop = connectLive("401909952", env, (c) => seen.push(c));
  return {
    sources,
    timers,
    seen,
    stop,
    last: () => seen[seen.length - 1] as LiveConn,
    setHidden: (h: boolean) => {
      st.hidden = h;
      vis?.();
    },
  };
}

describe("the stream connection", () => {
  it("takes a snapshot, applies the next delta, and closes at the final", () => {
    const h = harness();
    const s = h.sources[0] as FakeSource;
    s.emit("snapshot", snap());
    expect(h.last().phase).toBe("live");
    s.emit("delta", delta({ plays: [play(3)], n_plays: 41 }));
    expect(h.last().state?.plays.length).toBe(3);
    s.emit("final", { game_id: "x" });
    expect(h.last().phase).toBe("final");
    expect(s.closed).toBe(true);
    expect(h.sources.length).toBe(1); // no reconnect after the final
  });
  it("a gap in the sequence reconnects for a fresh snapshot", () => {
    const h = harness();
    (h.sources[0] as FakeSource).emit("snapshot", snap());
    (h.sources[0] as FakeSource).emit("delta", delta({ seq: 9 }));
    expect(h.sources.length).toBe(2);
    expect((h.sources[0] as FakeSource).closed).toBe(true);
  });
  it("stops streaming when the tab is hidden and resumes when it is shown", () => {
    const h = harness();
    (h.sources[0] as FakeSource).emit("snapshot", snap());
    h.setHidden(true);
    expect((h.sources[0] as FakeSource).closed).toBe(true);
    expect(h.last().phase).toBe("paused");
    expect(h.last().state).not.toBeNull(); // what we had stays on screen
    h.setHidden(false);
    expect(h.sources.length).toBe(2);
    (h.sources[1] as FakeSource).emit("snapshot", { ...snap(), seq: 7 });
    expect(h.last().phase).toBe("live");
    expect(h.last().state?.seq).toBe(7);
  });
  it("starts paused, not connected, when the tab opens hidden", () => {
    const h = harness(true);
    expect(h.sources.length).toBe(0);
    expect(h.last().phase).toBe("paused");
  });
  it("retries with a growing wait after a refusal; a dropped connection is left to the browser", () => {
    const h = harness();
    const s = h.sources[0] as FakeSource;
    s.readyState = 0; // connecting: the browser retries by itself
    s.onerror?.();
    expect(h.last().phase).toBe("reconnecting");
    expect(h.timers.length).toBe(0);
    s.readyState = CLOSED; // refused
    s.onerror?.();
    expect(h.last().phase).toBe("error");
    expect(h.timers[0]?.ms).toBe(BACKOFF_MS[0]);
    h.timers[0]?.fn();
    const s2 = h.sources[1] as FakeSource;
    s2.readyState = CLOSED;
    s2.onerror?.();
    expect(h.timers[1]?.ms).toBe(BACKOFF_MS[1]);
    h.stop();
    expect(s2.closed).toBe(true);
  });
});

// ------------------------------------------------------------------------------------------ what the live view may say

const SRC = __dirname;
const LIVE_FILES = ["live.ts", "useLive.ts", "useLiveData.ts", "LiveParts.tsx", "pages/Live.tsx"];

describe("the live view carries no prices and says where it comes from", () => {
  it("no live file reads or prints a price, odds, edge or fee", () => {
    for (const f of LIVE_FILES) {
      const text = readFileSync(join(SRC, f), "utf8")
        .split("\n")
        .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*") && !l.trim().startsWith("/*"))
        .join("\n");
      expect(text, f).not.toMatch(/over_price|under_price|american\(|market_p|price_book|\bodds\b|\bedge\b|\bfee\b|bookName/i);
    }
  });
  it("shows the attribution, the delay and the unproven label verbatim", () => {
    expect(ATTRIBUTION).toBe("Play-by-play: ESPN. Not affiliated.");
    expect(CHECKPOINT_LABEL).toBe("UNPROVEN: no live record; most nights the market already knows this.");
    const parts = readFileSync(join(SRC, "LiveParts.tsx"), "utf8");
    expect(parts).toContain("ATTRIBUTION");
    expect(parts).toContain("CHECKPOINT_LABEL");
    expect(parts).toContain("As of {ageText(age)} ago");
    expect(parts).toContain("LOCK_NOTE"); // the lock note on locked nights
    expect(parts).toContain("<CalTag source={t.source}"); // the label comes from the real source
  });
  it("does not render a raw chance (calibrated-only holds here too)", () => {
    for (const f of LIVE_FILES) expect(readFileSync(join(SRC, f), "utf8"), f).not.toMatch(/\bp_raw\b|p_over_raw|\.raw\b/);
  });
  it("the service worker leaves /api/live alone", () => {
    expect(readFileSync(join(SRC, "../public/sw.js"), "utf8")).toContain('url.pathname.startsWith("/api/live")) return;');
  });
});
