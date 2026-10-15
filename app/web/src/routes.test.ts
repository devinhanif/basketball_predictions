import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { LEG_STATS } from "./legTypes";
import { legHref, parse } from "./router";

describe("every leg link opens the leg route", () => {
  it("round-trips each stat and side, including awkward ids", () => {
    for (const stat of LEG_STATS) {
      for (const side of ["over", "under"] as const) {
        for (const gid of ["0022400001", "game with space", "a/b"]) {
          const r = parse(legHref(gid, 1630552, stat, side));
          expect(r).toEqual({ name: "leg", gameId: gid, playerId: 1630552, stat, side });
        }
      }
    }
  });

  it("falls back to Today for a malformed leg link rather than a blank page", () => {
    expect(parse("#/leg/g1/notanumber/pts").name).toBe("today");
    expect(parse("#/leg/g1/1/zzz").name).toBe("today");
  });
});

describe("no link in the source points at a route that does not exist", () => {
  const known = new Set(["", "game", "leg", "swipe", "picks", "parlay"]);
  const files = [
    ...readdirSync("src").filter((f) => f.endsWith(".tsx")).map((f) => join("src", f)),
    ...readdirSync("src/pages").filter((f) => f.endsWith(".tsx")).map((f) => join("src/pages", f)),
  ];
  it("every #/ href literal starts with a known route", () => {
    const seen: string[] = [];
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      for (const m of text.matchAll(/href=\{?[`"]#\/([a-z]*)/g)) {
        seen.push(`${f}: ${m[1] ?? ""}`);
        expect(known.has(m[1] ?? ""), `${f} links to #/${m[1] ?? ""}`).toBe(true);
      }
    }
    expect(seen.length).toBeGreaterThan(8);
  });

  it("each known route parses to itself", () => {
    expect(parse("#/").name).toBe("today");
    expect(parse("#/game/0022400001").name).toBe("game");
    expect(parse("#/swipe?date=2024-11-12")).toEqual({ name: "swipe", date: "2024-11-12" });
    expect(parse("#/picks").name).toBe("picks");
    expect(parse("#/parlay?date=2025-01-27")).toEqual({ name: "parlay", date: "2025-01-27" });
  });
});

describe("game links from a last-10 list", () => {
  it("gameHref opens the game route for any id", async () => {
    const { gameHref } = await import("./router");
    for (const id of ["0022400001", "a b", "x/y"]) {
      expect(parse(gameHref(id))).toEqual({ name: "game", id });
    }
  });
});
