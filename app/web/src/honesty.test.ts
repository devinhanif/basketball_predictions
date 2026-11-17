import { describe, expect, it } from "vitest";
import { hasPriceWords, isAbsent, NOT_IN_SNAPSHOT } from "./honesty";
import { helpHref, parse, plainName, sourceHref, topHref } from "./router";
import { t60Words } from "./Provenance";

describe("source route (where a number comes from)", () => {
  it("round-trips a game and a prop", () => {
    expect(parse(sourceHref("0022600245"))).toEqual({ name: "source", gameId: "0022600245", playerId: null, stat: null });
    expect(parse(sourceHref("0022600245", 1628369, "pts"))).toEqual({ name: "source", gameId: "0022600245", playerId: 1628369, stat: "pts" });
    expect(parse(sourceHref("a b/c", 7, "fg3m"))).toEqual({ name: "source", gameId: "a b/c", playerId: 7, stat: "fg3m" });
  });
  it("is not found for broken parts", () => {
    for (const h of ["#/source", "#/source/g/abc/pts", "#/source/g/1/zzz", "#/source/g/1", "#/source/g/1/pts/x", "#/source/%E0%A4%A"]) {
      expect(parse(h).name, h).toBe("notfound");
    }
  });
});

describe("scorecard, tests and help routes", () => {
  it("parse and keep their plain names", () => {
    expect(parse("#/scorecard")).toEqual({ name: "scorecard" });
    expect(parse("#/tests")).toEqual({ name: "tests" });
    expect(parse("#/scorecard/x").name).toBe("notfound");
    expect(parse("#/tests?x=1").name).toBe("notfound");
    expect(plainName({ name: "scorecard" })).toBe("scorecard");
    expect(topHref("scorecard", false)).toBe("#/scorecard");
    expect(topHref("scorecard", true)).toBe("#scorecard");
  });
  it("help lists all codes or opens one, with a search", () => {
    expect(parse("#/help/errors")).toEqual({ name: "help", code: null, q: null });
    expect(parse("#/help")).toEqual({ name: "help", code: null, q: null });
    expect(parse(helpHref("stale_data"))).toEqual({ name: "help", code: "stale_data", q: null });
    expect(parse(helpHref(null, "server"))).toEqual({ name: "help", code: null, q: "server" });
    expect(parse("#/help/errors/%3Cb%3E").name).toBe("notfound");
    expect(parse("#/help/other").name).toBe("notfound");
    expect(parse("#/help/errors/a/b").name).toBe("notfound");
    expect(topHref("help", false)).toBe("#/help/errors");
    expect(topHref("help", true)).toBe("#help");
    expect(parse("#help")).toEqual({ name: "help", code: null, q: null });
  });
});

describe("absent vs refused", () => {
  it("a snapshot without the answer, or a server without the route, is absent; a named refusal is not", () => {
    expect(isAbsent(404, { detail: NOT_IN_SNAPSHOT })).toBe(true);
    expect(isAbsent(404, { ok: false, code: "not_found", detail: "Not Found" })).toBe(true);
    expect(isAbsent(404, { detail: "Not Found" })).toBe(true);
    expect(isAbsent(404, { ok: false, code: "no_forecast" })).toBe(false);
    expect(isAbsent(503, { detail: NOT_IN_SNAPSHOT })).toBe(false);
    expect(isAbsent(200, {})).toBe(false);
  });
});

describe("plain words", () => {
  it("T-60 from the stored flag only", () => {
    expect(t60Words(true)).toMatch(/^Yes/);
    expect(t60Words(false)).toMatch(/^No/);
    expect(t60Words(null)).toMatch(/^Not known/);
  });
  it("price words are caught the way learn-dom catches them", () => {
    expect(hasPriceWords("a closing price per pick")).toBe(true);
    expect(hasPriceWords("Newest Pinnacle quote")).toBe(true);
    expect(hasPriceWords("On Kalshi, a withdrawal")).toBe(true);
    expect(hasPriceWords("integer-support CRPS, T-60 eligible scores")).toBe(false);
    expect(hasPriceWords(null)).toBe(false);
  });
});

describe("health strip age marks", async () => {
  const { isOld, OLD_AFTER_HOURS } = await import("./HealthStrip");
  const { agoText } = await import("./time");
  const now = new Date("2026-11-17T03:00:00Z");
  it("a quote three days old looks dead, a fresh one does not", () => {
    expect(isOld("2026-11-14T03:00:00Z", OLD_AFTER_HOURS.quote, now)).toBe(true);
    expect(agoText("2026-11-14T03:00:00Z", now)).toBe("3 days ago");
    expect(isOld("2026-11-17T00:45:47Z", OLD_AFTER_HOURS.quote, now)).toBe(false);
    expect(isOld(null, OLD_AFTER_HOURS.quote, now)).toBe(false);
  });
  it("the export uses the data line's own threshold", () => {
    expect(OLD_AFTER_HOURS.export).toBe(30);
  });
});

describe("distribution lookups (no arithmetic on probabilities)", async () => {
  const { overK, survivalAt } = await import("./Distribution");
  it("the over side of a half line is the next whole number; a whole line needs one more", () => {
    expect(overK(24.5)).toBe(25);
    expect(overK(25)).toBe(26);
    expect(overK(0.5)).toBe(1);
  });
  it("reads the stored count, or null outside the table", () => {
    const c = [10, 8, 5, 1];
    expect(survivalAt(c, 1)).toBe(10);
    expect(survivalAt(c, 4)).toBe(1);
    expect(survivalAt(c, 5)).toBeNull();
    expect(survivalAt(c, 0)).toBeNull();
  });
});

describe("scorecard rows", async () => {
  const { scoreRows } = await import("./pages/Scorecard");
  it("keeps shown means and counts-only rows apart, copying fields only", () => {
    const rows = scoreRows([
      {
        target: "pts",
        sentence: "s",
        models: [
          { model_name: "prod", role: "production", shown: true, withheld_reason: null, n_scored: 10, n_dates: 3, n_dnp: 1, n_scored_locked: 5, n_dates_locked: 2, metrics: { crps: { value: 3.1, lo: 3, hi: 3.2, n: 10, n_dates: 3, call: null } } },
          { model_name: "arm", role: "frozen_arm", shown: false, withheld_reason: "frozen", n_scored: 7, n_dates: 3, n_dnp: 0, n_scored_locked: 4, n_dates_locked: 2, metrics: {} },
        ],
        comparisons: [{ arm: "prod", comparator: "base", shown: true, withheld_reason: null, n_pairs: 9, n_dates: 3, n_pairs_locked: 1, n_dates_locked: 1, metrics: { crps: { value: -0.2, lo: -0.3, hi: -0.1, n: 9, n_dates: 3, call: "too_few_dates" } } }],
      },
    ]);
    expect(rows.map((r) => [r.label, r.shown, r.metric, r.n])).toEqual([
      ["prod", true, "crps", 10],
      ["arm", false, null, 7],
      ["prod minus base", true, "crps", 9],
    ]);
    expect(rows[1]?.withheld).toBe("frozen");
    expect(rows[2]?.m?.call).toBe("too_few_dates");
  });
});

describe("forward tests in the Learn lens", async () => {
  const { learnSafeTest } = await import("./pages/Tests");
  const base = { id: "X", source: "docs/x.md:1", as_of: null, arms: [], n_so_far: null, n_so_far_reason: null, scheduled_read: "L30", n_needed: "60 dates" };
  it("a test that speaks of prices is Market-only", () => {
    expect(learnSafeTest({ ...base, title: "Integer quantiles vs as-is" })).toBe(true);
    expect(learnSafeTest({ ...base, title: "Model side at the best available price" })).toBe(false);
    expect(learnSafeTest({ ...base, title: "Calibration at Pinnacle's line" })).toBe(false);
  });
});

describe("error help search", async () => {
  const { allCopies, searchCodes } = await import("./errorCodes");
  it("lists every registry code and finds by any of its words", () => {
    const all = allCopies();
    expect(all.length).toBeGreaterThan(20);
    expect(searchCodes(all, "").length).toBe(all.length);
    expect(searchCodes(all, "stale_data").map((c) => c.code)).toContain("stale_data");
    expect(searchCodes(all, "zzzz-nothing")).toEqual([]);
  });
});
