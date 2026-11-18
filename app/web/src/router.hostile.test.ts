// Hostile input for the hash router: thousands of seeded generated hashes. Every one must parse to a valid Route or the
// explicit not-found route, never throw; every href builder must round-trip, including awkward ids.
import { describe, expect, it } from "vitest";
import { LEG_STATS, isLegStat } from "./legTypes";
import { STATS } from "./types";
import {
  RECORD_TABS,
  gameHref,
  hashForRoute,
  legHref,
  liveHref,
  nightHref,
  parse,
  plainName,
  recordHref,
  startRoute,
  topHref,
  type Route,
  type TopName,
} from "./router";

/** mulberry32, so a failure names a seed that replays it. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const NAMES = ["", "game", "leg", "swipe", "picks", "parlay", "settings", "prefs", "signals", "hedge", "history", "record", "alerts", "live", "admin", "discover", "zzz", "GAME", "..", "main", "notfound", "__proto__", "constructor"];
const VALUES = [
  "0022400252", "1628378", "pts", "reb", "fg3m", "zzz", "", "%", "%E0%A4%A", "%2F", "%00", "a/b", "..", "é", "<script>alert(1)</script>",
  "x".repeat(300), "-1", "0", "1e3", "0x10", " 12", "NaN", "Infinity", "2024-11-20", "2024-02-30", "2024-13-01", "20241120", "garbage",
  "under", "over", "calibration", "guess", "replay", "%F0%9F%8F%80", "null", "undefined", "1.5", "9".repeat(30),
];
const KEYS = ["date", "side", "tab", "game", "player", "stat", "x", "", "%"];
const PREFIX = ["#", "#/", "", "##", "#//", "#/ ", "#\\"];
const CHARS = "abc/?&=%#+.-_ 019éø<>\"'\\";

function gen(r: () => number): string {
  const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(r() * xs.length)] as T;
  if (r() < 0.15) {
    let s = pick(PREFIX);
    const n = Math.floor(r() * 24);
    for (let i = 0; i < n; i++) s += CHARS.charAt(Math.floor(r() * CHARS.length));
    return s;
  }
  const segs = [pick(NAMES)];
  const extra = Math.floor(r() * 4);
  for (let i = 0; i < extra; i++) segs.push(pick(VALUES));
  let h = pick(PREFIX) + segs.join("/");
  if (r() < 0.6) {
    const q: string[] = [];
    const nq = 1 + Math.floor(r() * 3);
    for (let i = 0; i < nq; i++) q.push(`${pick(KEYS)}=${pick(VALUES)}`);
    h += `?${q.join(pick(["&", "&&", "?", ";"]))}`;
  }
  return h;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;
function realDate(s: string): boolean {
  if (!DATE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}
const okDate = (d: string | null): boolean => d === null || realDate(d);
const okId = (s: string): boolean => {
  if (s.length === 0 || s.length > 64) return false;
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) < 0x20) return false;
  return true;
};
const okPlayer = (n: number): boolean => Number.isSafeInteger(n) && n > 0;

/** Problems with a parsed route; empty when it is a valid Route. */
function problems(r: Route): string[] {
  const p: string[] = [];
  switch (r.name) {
    case "today":
    case "swipe":
    case "parlay":
      if (!okDate(r.date)) p.push(`bad date ${String(r.date)}`);
      break;
    case "game":
      if (!okId(r.id)) p.push("bad game id");
      break;
    case "leg":
      if (!okId(r.gameId)) p.push("bad game id");
      if (!okPlayer(r.playerId)) p.push(`bad player ${r.playerId.toString()}`);
      if (!isLegStat(r.stat)) p.push("bad stat");
      if (!["over", "under"].includes(r.side)) p.push("bad side");
      break;
    case "hedge":
      if (r.game !== null && !okId(r.game)) p.push("bad hedge game");
      if (r.player !== null && !okPlayer(r.player)) p.push(`bad hedge player ${r.player.toString()}`);
      if (r.stat !== null && !(STATS as readonly string[]).includes(r.stat)) p.push(`bad hedge stat ${r.stat}`);
      break;
    case "record":
      if (!RECORD_TABS.includes(r.tab)) p.push("bad tab");
      if (!okDate(r.date)) p.push(`bad date ${String(r.date)}`);
      break;
    case "live":
      if (r.id !== null && !okId(r.id)) p.push("bad live id");
      if (!okDate(r.date)) p.push(`bad date ${String(r.date)}`);
      break;
    case "notfound":
      if (typeof r.hash !== "string") p.push("not-found without its hash");
      break;
    case "picks":
    case "settings":
    case "signals":
    case "history":
    case "alerts":
    case "admin":
      break;
    default:
      p.push(`unknown route name ${(r as { name: string }).name}`);
  }
  return p;
}

describe("hostile hashes", () => {
  it("5,000 seeded hashes: each parses to a valid Route or not-found, never throws", () => {
    const r = rng(20261117);
    const failures: string[] = [];
    for (let i = 0; i < 5000; i++) {
      const h = gen(r);
      let route: Route;
      try {
        route = parse(h);
      } catch (e) {
        failures.push(`${JSON.stringify(h)} threw ${String(e)}`);
        continue;
      }
      const bad = problems(route);
      if (bad.length) failures.push(`${JSON.stringify(h)} -> ${JSON.stringify(route)}: ${bad.join(", ")}`);
      // The artifact frame helpers must accept whatever parse returns.
      expect(() => hashForRoute(route, true)).not.toThrow();
      expect(() => plainName(route)).not.toThrow();
    }
    expect(failures.slice(0, 20)).toEqual([]);
    expect(failures.length).toBe(0);
  });

  it("startRoute never throws, with or without a hash, on every landing screen", () => {
    const r = rng(7);
    for (let i = 0; i < 1000; i++) {
      for (const screen of ["today", "signals", "swipe", "picks"] as const) {
        const h = gen(r);
        expect(() => startRoute(h, screen)).not.toThrow();
        expect(problems(startRoute(h, screen))).toEqual([]);
      }
    }
    expect(startRoute("", "swipe").name).toBe("swipe");
  });

  it("unknown paths and bad ids are the explicit not-found route, keeping the hash", () => {
    for (const h of ["#/zzz", "#/zzz/yyy?x=1", "#/game/%E0%A4%A", "#/leg/g1/abc/pts", "#/leg/g1/1/zzz", "#/leg/g1/0/pts", "#/leg//1/pts", "#/?date=garbage", "#/record?date=2024-02-30", "#/hedge?player=abc", "#/hedge?stat=zzz", "#/swipe?date=2099-1-1", "#/live/%"]) {
      const route = parse(h);
      expect(route.name, h).toBe("notfound");
      if (route.name === "notfound") expect(route.hash).toBe(h);
    }
  });

  it("the plain names the artifact frame uses still open their pages", () => {
    const names: TopName[] = ["today", "signals", "hedge", "history", "record", "live", "alerts", "swipe", "picks", "admin", "settings"];
    for (const n of names) {
      expect(parse(topHref(n, true)).name).toBe(n);
      expect(parse(topHref(n, false)).name).toBe(n);
    }
    expect(parse("#main").name).toBe("today");
  });
});

describe("every href builder round-trips", () => {
  const ids = ["0022400252", "game with space", "a/b", "%", "<x>", "é", "?", "#", "a&b=c"];
  it("leg, game, live, record and night links parse back to what built them", () => {
    const r = rng(99);
    for (let i = 0; i < 2000; i++) {
      const gid = ids[Math.floor(r() * ids.length)] as string;
      const pid = 1 + Math.floor(r() * 2_000_000);
      const stat = LEG_STATS[Math.floor(r() * LEG_STATS.length)] ?? "pts";
      const side = r() < 0.5 ? "over" : "under";
      expect(parse(legHref(gid, pid, stat, side))).toEqual({ name: "leg", gameId: gid, playerId: pid, stat, side });
      expect(parse(gameHref(gid))).toEqual({ name: "game", id: gid });
      const date = `20${(10 + Math.floor(r() * 20)).toString()}-${(1 + Math.floor(r() * 12)).toString().padStart(2, "0")}-${(1 + Math.floor(r() * 28)).toString().padStart(2, "0")}`;
      expect(parse(liveHref(gid, date))).toEqual({ name: "live", id: gid, date });
      expect(parse(liveHref(null, date))).toEqual({ name: "live", id: null, date });
      expect(parse(nightHref(date))).toEqual({ name: "today", date });
      for (const tab of RECORD_TABS) {
        expect(parse(recordHref(tab, date))).toEqual({ name: "record", tab, date });
        expect(parse(recordHref(tab, null))).toEqual({ name: "record", tab, date: null });
      }
    }
  });
});
