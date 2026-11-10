import { beforeEach, describe, expect, it } from "vitest";
import { groupNews, leadText } from "./Knows";
import { learnHeadline } from "./headline";
import { hideBook } from "./slider";
import { MARKET_ONLY } from "./Shell";
import { DEFAULTS, KEYS, getPrefs, parseLens, resetPrefsCache, setPref, writeKey } from "./prefs";
import { GUESS_KEY, loadRounds, makeRound, saveRounds, scoreOf } from "./record";
import { parse, plainName, recordHref } from "./router";
import { TOUR_KEY, closeTour, isTourOpen, openTour, resetTour, tourSeen } from "./tourState";
import type { KnowsNews } from "./types";

beforeEach(() => {
  resetPrefsCache();
  resetTour();
});

describe("the lens", () => {
  it("is Learn for a new visitor, and anything unreadable is Learn", () => {
    expect(DEFAULTS.lens).toBe("learn");
    expect(getPrefs().lens).toBe("learn");
    expect(parseLens(null)).toBe("learn");
    expect(parseLens("nonsense")).toBe("learn");
    expect(parseLens("explore")).toBe("explore");
    expect(parseLens("market")).toBe("market");
  });
  it("is remembered, even when storage is blocked (the in-memory fallback)", () => {
    setPref("lens", "market");
    expect(getPrefs().lens).toBe("market");
    writeKey(KEYS.lens, "explore");
    resetPrefsCache(); // the cache is rebuilt from what was written (here the in-memory copy)
    expect(getPrefs().lens).toBe("learn"); // resetPrefsCache also clears the memory: a fresh visit
  });
});

describe("the tour", () => {
  it("opens, and finishing or skipping marks it seen so it does not return on its own", () => {
    expect(tourSeen()).toBe(false);
    openTour();
    expect(isTourOpen()).toBe(true);
    closeTour();
    expect(isTourOpen()).toBe(false);
    expect(tourSeen()).toBe(true);
    openTour(); // replay from the profile menu still works
    expect(isTourOpen()).toBe(true);
    expect(TOUR_KEY).toBe("ph.tour");
  });
});

describe("the track record route", () => {
  it("parses a tab and a night, and unknown tabs fall back to the replay", () => {
    expect(parse("#/record")).toEqual({ name: "record", tab: "replay", date: null });
    expect(parse("#/record?tab=calibration")).toEqual({ name: "record", tab: "calibration", date: null });
    expect(parse("#/record?tab=guess")).toEqual({ name: "record", tab: "guess", date: null });
    expect(parse("#/record?tab=wat&date=2026-11-08")).toEqual({ name: "record", tab: "replay", date: "2026-11-08" });
  });
  it("writes the hashes it parses", () => {
    expect(recordHref("replay", null)).toBe("#/record");
    expect(recordHref("guess", null)).toBe("#/record?tab=guess");
    expect(recordHref("replay", "2026-11-08")).toBe("#/record?date=2026-11-08");
    for (const [tab, date] of [["replay", null], ["calibration", null], ["guess", null], ["replay", "2024-11-05"]] as const) {
      expect(parse(recordHref(tab, date))).toEqual({ name: "record", tab, date });
    }
  });
  it("is a plain top-level view only on its first tab with no night", () => {
    expect(plainName(parse("#/record"))).toBe("record");
    expect(plainName(parse("#/record?tab=guess"))).toBeNull();
  });
});

describe("the guess score", () => {
  it("counts who was right, and gives a range only from 10 rounds", () => {
    const rounds = [
      makeRound("a", "over", "over", true), // both right
      makeRound("b", "over", "under", true), // only you
      makeRound("c", "under", "under", true), // the truth is over: both wrong
      makeRound("d", "under", "over", true), // only the model right
    ];
    expect(rounds[2]).toMatchObject({ you_right: false, model_right: false });
    const s = scoreOf(rounds);
    expect(s).toMatchObject({ n: 4, you: 2, model: 2, both: 1, onlyYou: 1, onlyModel: 1, neither: 1 });
    expect(s.youRange).toBeNull();
    const ten = scoreOf(Array.from({ length: 10 }, (_, i) => makeRound(`p${i.toString()}`, "over", "over", i < 6)));
    expect(ten.you).toBe(6);
    expect(ten.youRange).not.toBeNull();
    expect((ten.youRange?.[0] ?? 1) < 0.6 && (ten.youRange?.[1] ?? 0) > 0.6).toBe(true);
  });
  it("round-trips through storage and ignores damaged entries", () => {
    const r = makeRound("g:1:pts", "under", "over", false);
    saveRounds([r]);
    expect(loadRounds()).toEqual([r]);
    writeKey(GUESS_KEY, "not json");
    expect(loadRounds()).toEqual([]);
    writeKey(GUESS_KEY, JSON.stringify([r, { prop_id: 3 }, "x", null]));
    expect(loadRounds()).toEqual([r]);
    writeKey(GUESS_KEY, JSON.stringify({ not: "a list" }));
    expect(loadRounds()).toEqual([]);
  });
  it("a round with the truth 'over' marks over as right", () => {
    expect(makeRound("p", "over", "under", true)).toMatchObject({ you_right: true, model_right: false });
    expect(makeRound("p", "under", "under", false)).toMatchObject({ you_right: true, model_right: true });
  });
});

describe("the timeline words", () => {
  it("says how long before tip", () => {
    expect(leadText(0)).toBe("at tip");
    expect(leadText(42)).toBe("42 min before tip");
    expect(leadText(60)).toBe("1 h before tip");
    expect(leadText(330)).toBe("5 h 30 min before tip");
  });
  it("groups the report by time and folds first lines apart from changes", () => {
    const n = (id: number, at: string, first: boolean, status: string): KnowsNews => ({
      at_utc: at,
      lead_minutes: 100,
      player_id: id,
      name: `P${id.toString()}`,
      team: "AAA",
      status,
      previous: first ? null : "questionable",
      reason: null,
      himself: false,
      first_report: first,
    });
    const g = groupNews([n(1, "2024-11-12T20:00:00+00:00", false, "out"), n(2, "2024-11-12T17:00:00+00:00", true, "questionable"), n(3, "2024-11-12T17:00:00+00:00", true, "out")]);
    expect(g.map((x) => x.at)).toEqual(["2024-11-12T17:00:00+00:00", "2024-11-12T20:00:00+00:00"]);
    expect(g[0]?.first.length).toBe(2);
    expect(g[0]?.changes.length).toBe(0);
    expect(g[1]?.changes.length).toBe(1);
  });
});

describe("the Learn lens, app-wide", () => {
  it("the night's headline carries no contract count, edge, price or fee", () => {
    const h = learnHeadline(8);
    expect(h.verdict).toBe("keep");
    expect(h.text).toBe("8 games tonight · forecasts only · keep your money");
    expect(`${h.text} ${h.source}`).not.toMatch(/priced|edge after|fee|\bask\b|\bEV\b/i);
  });
  it("the market-only screens are the ones whose subject is prices and edges", () => {
    expect([...MARKET_ONLY].sort()).toEqual(["hedge", "history", "signals"]);
  });
});

describe("book names outside the Market lens", () => {
  it("reads the line, not the book", () => {
    expect(hideBook("No calibrated number: no two-sided DraftKings line was posted before tip.")).toBe("No calibrated number: no two-sided sportsbook line was posted before tip.");
    expect(hideBook("how often chances like this came in, DraftKings line, T493")).toBe("how often chances like this came in, sportsbook line, T493");
    expect(hideBook("Calibration: rung map x (DraftKings only)")).toBe("Calibration: rung map x (the sportsbook only)");
    expect(hideBook("no book named here")).toBe("no book named here");
  });
});
