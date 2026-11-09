import { gzipSync } from "node:zlib";
import { beforeEach, describe, expect, it } from "vitest";
import { createBakedStore, isBaked, seededShuffle, shardOf, type Baked } from "./bakedStore";
import { edgeLine, nearestFirst, type EdgeContract } from "./edges";
import { headline } from "./headline";
import { loadLedger, replaceLedger, type Pick } from "./ledger";
import { DEFAULTS, getPrefs, parseMode, parseScreen, parseTheme, resetPrefsCache, setPref } from "./prefs";
import { parse, plainName, startRoute, topHref } from "./router";
import {
  cleanDoc, cleanShared, docFromPicks, getShared, isUsableId, mergeLedgers, onIt, resetShared, startShared,
  toPick, type ClaudeLike, type SharedPick,
} from "./sharedPicks";
import type { ParlayState } from "./types";

function baked(): Baked {
  const pool = { date: "2026-11-07", clock_utc: "2026-11-07T20:00:00+00:00", note: "n", cards: ["a", "b", "c", "d"].map((p, i) => ({ prop_id: p, tip_utc: i < 3 ? "2026-11-07T22:00:00Z" : "2026-11-07T19:00:00Z" })) };
  return {
    format: 1, built_at: "x", data_as_of: "2026-11-08T16:35:05Z", export_date: "2026-11-08", data_version: "v",
    dates: ["2026-11-07", "2026-11-08"], swipe_nights: ["2026-11-07"],
    simple: {
      "/api/slates": { s: 200, b: [{ date: "2026-11-07" }, { date: "2026-11-08" }] },
      "/api/meta": { s: 200, b: { x: 1 } },
    },
    shards: {
      "g:G1": btoa(JSON.stringify({ "/api/games/G1": { s: 200, b: { id: "G1" } }, "/api/breakdown/G1/5/pts?side=over": { s: 200, b: { side: "over" } } })),
      "d:2026-11-07": btoa(JSON.stringify({ "/api/slates/2026-11-07/swipe": { s: 200, b: pool } })),
    },
  };
}
const plain = (b: string): Promise<string> => Promise.resolve(atob(b));

describe("static backend", () => {
  it("routes paths to shards", () => {
    expect(shardOf("/api/games/G1/result")).toBe("g:G1");
    expect(shardOf("/api/breakdown/G1/5/pts")).toBe("g:G1");
    expect(shardOf("/api/slates/2026-11-07/results")).toBe("d:2026-11-07");
    expect(shardOf("/api/meta")).toBeNull();
  });
  it("answers from simple entries, shards, and says 404 for anything else", async () => {
    const s = createBakedStore(baked(), plain);
    expect((await s.get("/api/meta")).body).toEqual({ x: 1 });
    expect((await s.get("/api/games/G1")).body).toEqual({ id: "G1" });
    expect((await s.get("/api/breakdown/G1/5/pts?side=over&line=20.5")).status).toBe(200); // line is ignored: the snapshot holds the posted line
    expect((await s.get("/api/breakdown/G1/5/pts?side=under")).status).toBe(404);
    expect((await s.get("/api/games/NOPE")).status).toBe(404);
    const real = createBakedStore({ ...baked(), shards: { "g:G9": gzipSync(Buffer.from(JSON.stringify({ "/api/games/G9": { s: 200, b: 9 } }))).toString("base64") } });
    expect((await real.get("/api/games/G9")).body).toBe(9); // a real gzip shard, opened with DecompressionStream
  });
  it("flags which nights have a deck", async () => {
    const s = createBakedStore(baked(), plain);
    const rows = (await s.get("/api/slates")).body as { date: string; deck: boolean }[];
    expect(rows.map((r) => r.deck)).toEqual([true, false]);
  });
  it("deals a seeded, reproducible deck of cards that have not tipped by the pinned clock", async () => {
    const s = createBakedStore(baked(), plain);
    const a = (await s.get("/api/slates/2026-11-07/swipe?seed=7&n=10")).body as { cards: { prop_id: string }[]; clock_utc: string };
    const b = (await s.get("/api/slates/2026-11-07/swipe?seed=7&n=10")).body as { cards: { prop_id: string }[] };
    expect(a.cards.map((c) => c.prop_id).sort()).toEqual(["a", "b", "c"]); // d tipped before the clock
    expect(a.cards).toEqual(b.cards);
    expect(a.clock_utc).toBe("2026-11-07T20:00:00+00:00");
    const empty = (await s.get("/api/slates/2026-11-08/swipe?seed=1")).body as { cards: unknown[]; note: string };
    expect(empty.cards).toEqual([]);
    expect(empty.note).toMatch(/not been played/);
    expect((await s.get("/api/slates/2020-01-01/swipe")).status).toBe(404);
  });
  it("shuffles deterministically and validates the file", () => {
    expect(seededShuffle([1, 2, 3, 4, 5], 3)).toEqual(seededShuffle([1, 2, 3, 4, 5], 3));
    expect(isBaked(baked())).toBe(true);
    expect(isBaked({ format: 2 })).toBe(false);
  });
});

describe("artifact router", () => {
  it("uses plain names for top-level views and keeps reserved names out", () => {
    expect(parse("#prefs")).toEqual({ name: "settings" });
    expect(parse("#settings")).toEqual({ name: "settings" });
    expect(parse("#swipe")).toEqual({ name: "swipe", date: null });
    expect(parse("#picks")).toEqual({ name: "picks" });
    expect(parse("#today").name).toBe("today");
    expect(plainName({ name: "settings" })).toBe("settings");
    expect(plainName({ name: "swipe", date: "2026-11-07" })).toBeNull();
    expect(plainName({ name: "game", id: "g" })).toBeNull();
    for (const n of ["today", "swipe", "picks", "settings"] as const) expect(topHref(n)).toMatch(/^#(\/|[a-z]+)/);
  });
  it("opens the device's default screen when there is no hash", () => {
    expect(startRoute("", "swipe").name).toBe("swipe");
    expect(startRoute("#/", "picks").name).toBe("picks");
    expect(startRoute("#/picks", "swipe").name).toBe("picks");
  });
});

describe("preferences", () => {
  beforeEach(() => { resetPrefsCache(); });
  it("parses junk to the defaults and survives blocked storage", () => {
    expect(parseTheme("pink")).toBe("system");
    expect(parseMode(null)).toBe("simple");
    expect(parseScreen("x")).toBe("today");
    expect(getPrefs()).toEqual(DEFAULTS); // no localStorage in node: the try/catch path
    setPref("mode", "analyst");
    expect(getPrefs().mode).toBe("analyst");
  });
});

const pick = (id: string, over: Partial<Pick> = {}): Pick => ({
  id, prop_id: id, slate_date: "2026-11-07", game_id: "G1", player: "A B", team: "ATL", opponent: "BKN", stat: "pts", line: 20.5,
  over_price: -110, under_price: -110, tip_utc: "2026-11-07T22:00:00Z", side: "over", made_at: "2026-11-07T20:00:00.000Z",
  slate_clock: "2026-11-07T20:00:00.000Z", response_ms: 10, source: "swipe", saw_model: false, ...over,
});

describe("shared picks: validation", () => {
  const good = docFromPicks([pick("G1:1:pts")]).picks[0] as SharedPick;
  it("accepts a well-formed pick and nothing money-like travels", () => {
    expect(cleanShared(good)).toEqual(good);
    expect(Object.keys(good).some((k) => /stake|unit|amount|bankroll|size/.test(k))).toBe(false);
  });
  it("rejects bad shapes and caps lengths", () => {
    expect(cleanShared({ ...good, side: "skip" })).toBeNull();
    expect(cleanShared({ ...good, stat: "pra" })).toBeNull();
    expect(cleanShared({ ...good, line: Infinity })).toBeNull();
    expect(cleanShared({ ...good, player: "x".repeat(500) })).toBeNull();
    expect(cleanShared({ ...good, made_at: "not a date" })).toBeNull();
    expect(cleanShared("hello")).toBeNull();
  });
  it("keeps the good picks of a hostile document, once per prop, and ignores the wrong version", () => {
    expect(cleanDoc({ v: 1, picks: [good, good, { evil: 1 }, null] })).toEqual([good]);
    expect(cleanDoc({ v: 2, picks: [good] })).toEqual([]);
    expect(cleanDoc(null)).toEqual([]);
    expect(isUsableId("u_abc-1")).toBe(true);
    expect(isUsableId("a/b")).toBe(false);
    expect(isUsableId("")).toBe(false);
  });
  it("leaves skips out of the shared document and merges the earlier answer", () => {
    expect(docFromPicks([pick("a", { side: "skip" }), pick("b")]).picks.map((p) => p.prop_id)).toEqual(["b"]);
    const remote = [{ ...(docFromPicks([pick("a", { made_at: "2026-11-07T19:00:00.000Z" })]).picks[0] as SharedPick) }];
    const merged = mergeLedgers([pick("a"), pick("z")], remote);
    expect(merged.find((p) => p.prop_id === "a")?.made_at).toBe("2026-11-07T19:00:00.000Z");
    expect(merged).toHaveLength(2);
    expect(toPick(remote[0] as SharedPick).source).toBe("swipe");
  });
  it("names who is on a prop", () => {
    const s = { ...getShared(), others: { u_s: [good] }, names: { u_s: "Sam" } };
    expect(onIt("G1:1:pts", [pick("G1:1:pts")], s).map((w) => w.who)).toEqual(["you", "Sam"]);
    expect(onIt("G1:1:pts", [], { ...s, names: {} }).map((w) => w.who)).toEqual(["Someone"]);
  });
});

describe("shared picks: the platform db", () => {
  beforeEach(() => { resetShared(); replaceLedger([]); });
  const queue: { fn: () => void; ms: number; h: number }[] = [];
  const fire = (ms: number): void => {
    for (const t of queue.filter((q) => q.ms === ms)) { queue.splice(queue.indexOf(t), 1); t.fn(); }
  };
  const timers = {
    setTimeout: (fn: () => void, ms: number) => { const h = queue.length + 1; queue.push({ fn, ms, h }); return h; },
    clearTimeout: (h: unknown) => { const i = queue.findIndex((q) => q.h === h); if (i >= 0) queue.splice(i, 1); },
  };

  it("falls back to this device when there is no claude, or db is null", async () => {
    await startShared({ claude: undefined, ...timers });
    expect(getShared().status).toBe("off");
    resetShared();
    await startShared({ claude: { use: () => Promise.resolve(null) }, ...timers });
    expect(getShared().status).toBe("off");
    expect(getShared().canWrite).toBe(false);
    replaceLedger([pick("x")]); // still works locally
    expect(loadLedger()).toHaveLength(1);
  });

  it("subscribes once, merges a saved document before writing, and writes one document at a time", async () => {
    const writes: { id: string; n: number }[] = [];
    let listeners = 0;
    const hold: { push?: (s: unknown) => void } = {};
    const remoteMine = docFromPicks([pick("old", { made_at: "2026-11-06T10:00:00.000Z" })]);
    const sam = docFromPicks([pick("old", { side: "under" })]);
    const db = {
      collection: () => ({
        onSnapshot: (next: (s: unknown) => void) => { listeners++; hold.push = next; return () => undefined; },
        doc: (id: string) => ({ set: (d: { picks: unknown[] }) => { writes.push({ id, n: d.picks.length }); return Promise.resolve(); } }),
      }),
    };
    const user = { id: () => Promise.resolve("u_me"), profiles: (ids: readonly string[]) => Promise.resolve(Object.fromEntries(ids.map((i) => [i, { id: i, name: "" }]))) };
    const claude: ClaudeLike = { use: (n) => Promise.resolve(n === "db" ? db : user) };
    replaceLedger([pick("new")]);
    await startShared({ claude, ...timers });
    expect(listeners).toBe(1);
    expect(getShared().status).toBe("loading");
    expect(writes).toHaveLength(0); // nothing is written before the first definitive snapshot
    fire(10_000);
    expect(getShared().status).toBe("timeout"); // 10 s with no answer: a designed state, the subscription stays
    hold.push?.({
      metadata: { fromCache: false },
      docs: [
        { id: "u_me", exists: true, data: () => remoteMine },
        { id: "u_sam", exists: true, data: () => sam },
        { id: "bad/id", exists: true, data: () => sam },
      ],
    });
    expect(getShared().status).toBe("live");
    expect(Object.keys(getShared().others)).toEqual(["u_sam"]);
    expect(loadLedger().map((p) => p.prop_id).sort()).toEqual(["new", "old"]); // merged, not replaced
    fire(400); // the debounce
    await new Promise((r) => setTimeout(r, 20));
    expect(writes).toEqual([{ id: "u_me", n: 2 }]);
    await new Promise((r) => setTimeout(r, 20));
    expect(getShared().names["u_sam"]).toBe("Someone"); // an unresolved name renders as "Someone"
    expect(listeners).toBe(1);
  });
});

describe("headline and edge line (numbers only from stored fields)", () => {
  const eng = (over: Partial<ParlayState> = {}): ParlayState => ({
    state: "C", title: "No parlay today", headline_verdict: "no_positive_ev_found", verdict_line: "Keep your money.", as_of: "", report_date: "2026-11-08",
    source: "", engine: "", reason: "", counts: { n_contracts: 2956, n_flagged_positive: 0 }, gate: null, model_weight: null, model_weight_note: "",
    fee_model: null, price_basis: "", historical_note: "", disclaimer: "", ...over,
  });
  it("states the night's answer", () => {
    expect(headline({ games: 7, engine: eng(), speaks: true, tickets: null }).text).toBe("7 games · 0 of 2,956 priced contracts clear an edge after fees · keep your money");
    expect(headline({ games: 1, engine: eng(), speaks: false, tickets: null }).text).toBe("1 game · no pricing check is stored for this night · keep your money");
    expect(headline({ games: 3, engine: null, speaks: false, tickets: null }).text).toContain("unavailable");
    const a = headline({ games: 7, engine: eng({ state: "A", headline_verdict: "positive_ev", counts: { n_contracts: 10, n_flagged_positive: 2 } }), speaks: true, tickets: null });
    expect(a.verdict).toBe("flagged");
    expect(a.text).toContain("2 of 10");
    expect(a.text).toContain("keep your money"); // no proven track record in the data
  });
  const c: EdgeContract = { side: "over", threshold: 6, title: "t", ticker: "k", price: 0.81, fees: 0.02, ev: -0.075, ev_low: -0.075, model_prob: 0.755, prob_lo: 0.755, prob_hi: 0.755, order_type: "taker", price_ts: "" };
  it("prints model [range] vs ask + fee = edge", () => {
    expect(edgeLine(c)).toBe("Model 75.5% [range not stored] vs ask 81c + fee 2c = edge -7.5c per $1");
    expect(edgeLine({ ...c, prob_lo: 0.7, prob_hi: 0.8 })).toContain("[70% to 80%]");
    expect(edgeLine({ ...c, prob_lo: null, prob_hi: null })).toContain("range not stored");
    expect(nearestFirst([{ ...c, threshold: 9 }, c], 5.5)[0]?.threshold).toBe(6);
  });
});
