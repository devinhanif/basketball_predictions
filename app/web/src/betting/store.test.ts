// The workspace store: load, persist, survive a "reload" (a new store over the same storage), failed saves and invalid
// imports that keep data, scoped keys, and actions that never reach the network.
import { afterEach, describe, expect, it, vi } from "vitest";
import { storageKey, type StorageLike } from "./repository";
import { sampleQuote } from "./sample";
import { betActions, createBetStore } from "./store";
import { marketFromFacts } from "./snapshots";
import type { Market } from "./types";

const T = "2024-11-05T02:00:00.000Z";
const nowIso = (): string => T;

function memory(): StorageLike & { map: Map<string, string>; failWrites: boolean } {
  const map = new Map<string, string>();
  const s = {
    map,
    failWrites: false,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (s.failWrites) throw new Error("quota");
      map.set(k, v);
    },
  };
  return s;
}

function open(storage: StorageLike, scope: { kind: "device" } | { kind: "user"; userId: string } = { kind: "device" }) {
  const store = createBetStore({ storage, scope, nowIso });
  return { store, actions: betActions(store, nowIso) };
}

const market = (): Market => {
  const r = marketFromFacts({ gameId: "0022400123", date: "2024-11-04", playerId: 1630224, playerName: "Jalen Green", stat: "ast", line: 3.5, eventLabel: "HOU @ ORL" });
  if (!r.ok) throw new Error(r.text);
  return r.market;
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("betting workspace store", () => {
  it("loads an empty workspace without seeding anything", async () => {
    const { store } = open(memory());
    await store.load();
    const ws = store.get().workspace;
    expect(ws?.draft.legs).toEqual([]);
    expect(ws?.picks).toEqual([]);
    expect(store.get().phase).toBe("ready");
  });

  it("a pick added survives a reload, and adding it again does not duplicate it", async () => {
    const mem = memory();
    const a = open(mem);
    await a.store.load();
    const first = await a.actions.addPick(market(), "yes", "leg");
    expect(first).toMatchObject({ ok: true });
    const again = await a.actions.addPick(market(), "yes", "game");
    expect(again.ok && again.text).toBe("Already in your slip.");
    const b = open(mem);
    await b.store.load();
    expect(b.store.get().workspace?.picks.map((p) => [p.market.playerName, p.side, p.from])).toEqual([["Jalen Green", "yes", "leg"]]);
  });

  it("re-enables a switched-off planned leg instead of adding a pick", async () => {
    const { store, actions } = open(memory());
    await store.load();
    const { quote, market: m } = sampleQuote("bobby", "kalshi", "yes");
    await actions.addQuote(quote, m);
    const legId = store.get().workspace?.draft.legs[0]?.id ?? "";
    await actions.toggle(legId);
    expect(store.get().workspace?.draft.legs[0]?.enabled).toBe(false);
    const r = await actions.addPick(m, "yes", "game");
    expect(r.ok && r.text).toMatch(/Switched back on/);
    expect(store.get().workspace?.draft.legs.length).toBe(1);
    expect(store.get().workspace?.draft.legs[0]?.enabled).toBe(true);
    expect(store.get().workspace?.picks.length).toBe(0);
  });

  it("pricing a pick in Compare replaces the pick with a planned leg", async () => {
    const { store, actions } = open(memory());
    await store.load();
    const { quote, market: m } = sampleQuote("jalen", "draftkings", "yes");
    await actions.addPick(m, "yes", "leg");
    expect(store.get().workspace?.picks.length).toBe(1);
    await actions.addQuote(quote, m);
    expect(store.get().workspace?.picks.length).toBe(0);
    expect(store.get().workspace?.draft.legs.length).toBe(1);
  });

  it("a failed save keeps the edit on screen (unsaved), keeps stored data, and retry saves it", async () => {
    const mem = memory();
    const { store, actions } = open(mem);
    await store.load();
    await actions.addPick(market(), "yes", "leg");
    const before = mem.map.get(storageKey({ kind: "device" }));
    mem.failWrites = true;
    const r = await actions.addPick(market(), "no", "leg");
    expect(r.ok).toBe(false);
    expect(store.get().dirty).toBe(true);
    expect(store.get().workspace?.picks.length).toBe(2);
    expect(mem.map.get(storageKey({ kind: "device" }))).toBe(before);
    mem.failWrites = false;
    expect((await store.retry()).ok).toBe(true);
    expect(store.get().dirty).toBe(false);
    const reread = open(mem);
    await reread.store.load();
    expect(reread.store.get().workspace?.picks.length).toBe(2);
  });

  it("discard after a failed save goes back to what the browser holds", async () => {
    const mem = memory();
    const { store, actions } = open(mem);
    await store.load();
    mem.failWrites = true;
    await actions.addPick(market(), "yes", "leg");
    store.discard();
    expect(store.get().workspace?.picks.length).toBe(0);
    expect(store.get().dirty).toBe(false);
  });

  it("an invalid import changes nothing and says why; a valid one replaces the workspace", async () => {
    const mem = memory();
    const { store, actions } = open(mem);
    await store.load();
    await actions.addPick(market(), "yes", "leg");
    const exported = await store.exportJson();
    expect(exported.ok).toBe(true);
    const before = mem.map.get(storageKey({ kind: "device" }));
    for (const bad of ["{oops", JSON.stringify({ schema: "ph.betting.workspace", version: 1, document: { draft: {} } })]) {
      const r = await store.importJson(bad);
      expect(r.ok).toBe(false);
      expect(r.ok ? "" : r.text).toMatch(/Nothing was changed/);
      expect(mem.map.get(storageKey({ kind: "device" }))).toBe(before);
      expect(store.get().workspace?.picks.length).toBe(1);
    }
    await actions.removePick(store.get().workspace?.picks[0]?.id ?? "");
    expect(store.get().workspace?.picks.length).toBe(0);
    const r = await store.importJson(exported.ok ? exported.json : "");
    expect(r.ok).toBe(true);
    expect(store.get().workspace?.picks.length).toBe(1);
  });

  it("a workspace written before picks existed still loads (picks read as empty)", async () => {
    const mem = memory();
    const { store } = open(mem);
    await store.load();
    const doc = { draft: { legs: [], entry: { stake: 10, multiplier: 3 }, title: "", thesis: "", tags: [], origin: "user" }, ideas: [], manualQuotes: [] };
    mem.map.set(storageKey({ kind: "device" }), JSON.stringify({ schema: "ph.betting.workspace", version: 1, scope: "device", revision: "r1", savedAt: T, lastMutation: null, document: doc }));
    const again = open(mem);
    await again.store.load();
    expect(again.store.get().workspace?.picks).toEqual([]);
  });

  it("scopes never share data", async () => {
    const mem = memory();
    const a = open(mem, { kind: "user", userId: "u1" });
    await a.store.load();
    await a.actions.addPick(market(), "yes", "leg");
    const b = open(mem, { kind: "user", userId: "u2" });
    await b.store.load();
    expect(b.store.get().workspace?.picks.length).toBe(0);
    const d = open(mem);
    await d.store.load();
    expect(d.store.get().workspace?.picks.length).toBe(0);
  });

  it("mark placed is a local record: no network call of any kind", async () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    const { store, actions } = open(memory());
    await store.load();
    const { quote, market: m } = sampleQuote("jalen", "draftkings", "yes");
    await actions.addQuote(quote, m, 10);
    const r = await actions.markPlaced();
    expect(r.ok).toBe(true);
    expect(store.get().workspace?.draft.legs[0]?.role).toBe("existing");
    expect(store.get().workspace?.draft.legs[0]?.placedAt).toBe(T);
    expect(spy).not.toHaveBeenCalled();
  });

  it("quick successive edits are all kept (none dropped while a save is in flight)", async () => {
    const { store, actions } = open(memory());
    await store.load();
    const m = market();
    await Promise.all([actions.addPick(m, "yes", "leg"), actions.addPick(m, "no", "leg"), actions.setNotes({ title: "t" })]);
    expect(store.get().workspace?.picks.length).toBe(2);
    expect(store.get().workspace?.draft.title).toBe("t");
  });
});
