import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearViewer } from "./adminActions";
import { buckets, summarize, type HedgeLeg } from "./hedge";
import { markIdsRead, resetUserState, startUserStateSync } from "./userState";
import { CAP_TIMEOUT_MS, resolvePlatform, type Platform } from "./platform";
import { removePhoto, resetPhotos, savePhoto, validAssetId } from "./photos";
import { hashForRoute, parse, topHref, type TopName } from "./router";
import { resetWatch, startWatchSync, toggleWatch } from "./watch";

interface Op {
  op: string;
  path: string;
}
type Snap = { exists: boolean; data(): undefined; metadata: { fromCache: boolean } };

/** A fake platform that records every db write and delete with its exact path. Snapshots answer once, empty and definitive. */
function fake(uid = "u_me"): { log: Op[]; platform: Platform } {
  const log: Op[] = [];
  const ref = (path: string) => ({
    get: () => Promise.resolve({ exists: false, data: () => undefined }),
    set: () => {
      log.push({ op: "set", path });
      return Promise.resolve();
    },
    delete: () => {
      log.push({ op: "delete", path });
      return Promise.resolve();
    },
    onSnapshot: (next: (s: Snap) => void) => {
      void Promise.resolve().then(() => { next({ exists: false, data: () => undefined, metadata: { fromCache: false } }); });
      return () => undefined;
    },
  });
  const db = {
    doc: ref,
    collection: (c: string) => ({
      onSnapshot: (next: (s: { docs: never[]; metadata: { fromCache: boolean } }) => void) => {
        void Promise.resolve().then(() => { next({ docs: [], metadata: { fromCache: false } }); });
        return () => undefined;
      },
      doc: (id: string) => ref(`${c}/${id}`),
    }),
  };
  const user = { id: () => Promise.resolve(uid), profiles: () => Promise.resolve({}) };
  const assets = { upload: () => Promise.resolve({ id: "a".repeat(32), url: "x" }), delete: () => Promise.resolve({ deleted: true }) };
  return { log, platform: { db, user, assets, downloads: null } as unknown as Platform };
}

beforeEach(() => {
  (globalThis as unknown as { window: unknown }).window = globalThis;
  vi.useFakeTimers();
  resetWatch();
  resetUserState();
  resetPhotos();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("every db write goes to the exact path the page's rules allow, and to nothing else", () => {
  it("watchlist: only watch/<own id>", async () => {
    const { log, platform } = fake();
    toggleWatch({ key: "k1", label: "L", sub: "" });
    await startWatchSync(platform);
    await vi.advanceTimersByTimeAsync(2000);
    toggleWatch({ key: "k2", label: "L2", sub: "" });
    await vi.advanceTimersByTimeAsync(2000);
    expect(log.length).toBeGreaterThan(0);
    expect(new Set(log.map((o) => `${o.op} ${o.path}`))).toEqual(new Set(["set watch/u_me"]));
  });
  it("private read state: only data/users/<own id>/state", async () => {
    const { log, platform } = fake();
    await startUserStateSync(platform);
    await vi.advanceTimersByTimeAsync(1000);
    markIdsRead(["alert:1"]);
    await vi.advanceTimersByTimeAsync(2000);
    expect(log.length).toBeGreaterThan(0);
    expect(new Set(log.map((o) => `${o.op} ${o.path}`))).toEqual(new Set(["set data/users/u_me/state"]));
  });
  it("photos: photos/<player id> to set, the same to remove; a non-image writes nothing", async () => {
    const { log, platform } = fake();
    const img = new File([new Uint8Array([1, 2, 3])], "a.png", { type: "image/png" });
    expect(await savePhoto(7, img, platform)).toEqual({ ok: true });
    expect(await removePhoto(7, platform)).toEqual({ ok: true });
    expect(log).toEqual([{ op: "set", path: "photos/7" }, { op: "delete", path: "photos/7" }]);
    const bad = await savePhoto(8, new File(["x"], "a.txt", { type: "text/plain" }), platform);
    expect(bad.ok).toBe(false);
    expect(log).toHaveLength(2);
  });
  it("owner clear: deletes that viewer's picks and watchlist documents and nothing more", async () => {
    const { log, platform } = fake();
    expect(await clearViewer(platform, "u_sam")).toBe(true);
    expect(log).toEqual([{ op: "delete", path: "picks/u_sam" }, { op: "delete", path: "watch/u_sam" }]);
    expect(await clearViewer({ ...platform, db: null }, "u_sam")).toBe(false);
  });
  it("accepts the asset ids the platform may issue (opaque tokens), not path tricks", () => {
    expect(validAssetId("a".repeat(32))).toBe(true);
    expect(validAssetId("Ab_-" + "z".repeat(20))).toBe(true);
    expect(validAssetId("../../etc/passwd")).toBe(false);
    expect(validAssetId("a/b".padEnd(32, "x"))).toBe(false);
    expect(validAssetId("short")).toBe(false);
  });
});

describe("capabilities that never answer", () => {
  it("resolves every capability to null after 10 s so the page still renders", async () => {
    const never = { use: () => new Promise<unknown>(() => undefined) };
    const p = resolvePlatform(never);
    await vi.advanceTimersByTimeAsync(CAP_TIMEOUT_MS + 1);
    expect(await p).toEqual({ db: null, user: null, assets: null, downloads: null });
  });
  it("hands back a capability that answers, and null for one that throws or is missing", async () => {
    const claude = { use: (n: string) => (n === "db" ? Promise.resolve({ marker: 1 }) : n === "user" ? Promise.reject(new Error("no")) : Promise.resolve(null)) };
    const p = await resolvePlatform(claude);
    expect(p.db).toEqual({ marker: 1 });
    expect(p.user).toBeNull();
    expect(await resolvePlatform(undefined)).toEqual({ db: null, user: null, assets: null, downloads: null });
  });
});

describe("artifact hashes", () => {
  const TOP: TopName[] = ["today", "signals", "hedge", "history", "alerts", "swipe", "picks", "admin", "settings"];
  const RESERVED = ["settings", "sidebar", "customize", "upgrade", "downloads"];
  it("top-level hashes are plain lowercase names, never reserved, and parse back to their screen", () => {
    for (const n of TOP) {
      const h = topHref(n, true);
      expect(h).toMatch(/^#[a-z]+$/);
      expect(RESERVED.includes(h.slice(1))).toBe(false);
      expect(h.startsWith("#comment-")).toBe(false);
      expect(parse(h).name).toBe(n === "today" ? "today" : n);
    }
  });
  it("detail views and dated or filtered views never reach the hash (no '#/' and no query string)", () => {
    expect(hashForRoute({ name: "game", id: "0022600196" }, true)).toBeNull();
    expect(hashForRoute({ name: "leg", gameId: "g", playerId: 1, stat: "pts", side: "over" }, true)).toBeNull();
    expect(hashForRoute({ name: "parlay", date: null }, true)).toBeNull();
    expect(hashForRoute({ name: "today", date: "2026-11-08" }, true)).toBeNull();
    expect(hashForRoute({ name: "swipe", date: "2026-11-07" }, true)).toBeNull();
    expect(hashForRoute({ name: "hedge", game: "g", player: 1, stat: "ast" }, true)).toBeNull();
    expect(hashForRoute({ name: "today", date: null }, true)).toBe("#discover");
    expect(hashForRoute({ name: "settings" }, true)).toBe("#prefs");
  });
});

describe("position lab: fee unknown and the zero bucket", () => {
  const leg = (o: Partial<HedgeLeg>): HedgeLeg => ({ id: "x", kind: "stat", side: "yes", label: "l", threshold: 3, contracts: 1, ask_c: 40, fee_c: 0, fee_set: false, source: "entered", ...o });
  it("an empty fee is flagged: results are before fee, not a silent zero fee", () => {
    const s = summarize([leg({})], { home: "H", away: "A" });
    expect(s?.before_fee).toBe(true);
    expect(summarize([leg({ fee_set: true, fee_c: 2 })], { home: "H", away: "A" })?.before_fee).toBe(false);
  });
  it("a first threshold of 0 does not make a second '0' bucket", () => {
    expect(buckets([leg({ threshold: 0 })]).map((b) => b.label)).toEqual(["0+"]);
    expect(buckets([leg({ threshold: 1 })]).map((b) => b.label)).toEqual(["0", "1+"]);
  });
});
