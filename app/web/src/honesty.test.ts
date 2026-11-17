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
