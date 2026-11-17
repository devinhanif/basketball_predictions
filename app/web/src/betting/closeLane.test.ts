// Cross-tab sync, the paper-outcome history (with its schema step), the Compare ordering guard, and the long-content and
// compatibility rules for this module.
import { describe, expect, it } from "vitest";
import { buildDemoDraft, sampleMarketId, sampleQuote } from "./sample";
import { migrate, SLIP_CODEC, storageKey, type StorageLike } from "./repository";
import { createSlipWorkspace, recordOutcomes, validateSlipWorkspace } from "./slipModel";
import { betActions, createBetStore } from "./store";
import { marketFromFacts } from "./snapshots";
import type { Market, SavedIdea, SlipWorkspace } from "./types";

const T = "2026-11-08T12:00:00.000Z";
const COLLIN = sampleMarketId("collin");

type Mem = { map: Map<string, string>; failWrites: boolean; getItem: (k: string) => string | null; setItem: (k: string, v: string) => void };
function memory(): Mem {
  const map = new Map<string, string>();
  const m: Mem = {
    map,
    failWrites: false,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => {
      if (m.failWrites) throw new Error("quota");
      map.set(k, v);
    },
  };
  return m;
}
function open(storage: StorageLike, scope: { kind: "device" } | { kind: "user"; userId: string } = { kind: "device" }) {
  let n = 0;
  const nowIso = (): string => new Date(Date.parse(T) + 1000 * n++).toISOString();
  const store = createBetStore({ storage, scope, nowIso });
  return { store, actions: betActions(store, nowIso) };
}
const market = (): Market => {
  const r = marketFromFacts({ gameId: "0022400123", date: "2024-11-04", playerId: 1630224, playerName: "Jalen Green", stat: "ast", line: 3.5, eventLabel: "HOU @ ORL" });
  if (!r.ok) throw new Error(r.text);
  return r.market;
};
const ok = <R extends { ok: boolean }>(r: R): Extract<R, { ok: true }> => {
  if (!r.ok) throw new Error(JSON.stringify(r));
  return r as Extract<R, { ok: true }>;
};
const withIdea = (): SlipWorkspace => {
  const idea: SavedIdea = { ...buildDemoDraft(), id: "saved-demo", createdAt: T, status: "paper", outcomes: {} };
  return { ...createSlipWorkspace(), draft: buildDemoDraft(), ideas: [idea] };
};
const idea0 = (ws: SlipWorkspace): SavedIdea => {
  const i = ws.ideas[0];
  if (!i) throw new Error("no idea");
  return i;
};

describe("cross-tab sync (a storage event reaches the store)", () => {
  it("a pick added in another tab appears here without a reload, and a foreign key is ignored", async () => {
    const mem = memory();
    const a = open(mem);
    const b = open(mem);
    await a.store.load();
    await b.store.load();
    await a.actions.addPick(market(), "yes", "leg");
    expect(b.store.get().workspace?.picks.length).toBe(0);
    expect(await b.store.externalChange("some.other.key")).toBe(false);
    expect(b.store.get().workspace?.picks.length).toBe(0);
    expect(await b.store.externalChange(storageKey({ kind: "device" }))).toBe(true);
    expect(b.store.get().workspace?.picks.length).toBe(1);
    // The other tab can now edit on top of it (its revision is current), no conflict.
    expect(await b.actions.removePick(b.store.get().workspace?.picks[0]?.id ?? "")).toMatchObject({ ok: true });
  });

  it("another viewer's key never changes this viewer's workspace", async () => {
    const mem = memory();
    const a = open(mem, { kind: "user", userId: "u1" });
    await a.store.load();
    expect(await a.store.externalChange(storageKey({ kind: "user", userId: "u2" }))).toBe(false);
  });

  it("an unsaved edit on screen is not overwritten by the other tab; the late save reports the conflict", async () => {
    const mem = memory();
    const a = open(mem);
    const b = open(mem);
    await a.store.load();
    await b.store.load();
    await a.actions.addPick(market(), "yes", "leg");
    // b has an edit that failed to save (dirty), then the other tab's write arrives.
    mem.failWrites = true;
    const failed = await b.actions.setNotes({ title: "mine" });
    mem.failWrites = false;
    expect(failed.ok).toBe(false);
    expect(b.store.get().dirty).toBe(true);
    expect(await b.store.externalChange(storageKey({ kind: "device" }))).toBe(false);
    expect(b.store.get().workspace?.draft.title).toBe("mine");
  });

  it("an external change waits behind an edit in flight (the superseded-load guard), then takes the newest copy", async () => {
    const mem = memory();
    const a = open(mem);
    const b = open(mem);
    await a.store.load();
    await b.store.load();
    const mine = b.actions.setNotes({ title: "first" });
    const sync = b.store.externalChange(null);
    await mine;
    await sync;
    expect(b.store.get().workspace?.draft.title).toBe("first");
    expect(b.store.get().dirty).toBe(false);
  });
});

describe("history of an idea's paper outcomes", () => {
  it("records, corrects and reopens, each with its time and outcomes; an identical save adds nothing", () => {
    let ws = withIdea();
    ws = ok(recordOutcomes(ws, "saved-demo", { [COLLIN]: "no" }, "settled", [], "2026-11-09T01:00:00.000Z")).workspace;
    expect(idea0(ws).history).toEqual([{ at: "2026-11-09T01:00:00.000Z", action: "recorded", status: "settled", outcomes: { [COLLIN]: "no" }, voids: [] }]);
    ws = ok(recordOutcomes(ws, "saved-demo", { [COLLIN]: "no" }, "settled", [], "2026-11-09T02:00:00.000Z")).workspace;
    expect(idea0(ws).history?.length).toBe(1);
    ws = ok(recordOutcomes(ws, "saved-demo", { [COLLIN]: "yes" }, "settled", [], "2026-11-09T03:00:00.000Z")).workspace;
    expect(idea0(ws).history?.map((h) => h.action)).toEqual(["recorded", "corrected"]);
    ws = ok(recordOutcomes(ws, "saved-demo", {}, "paper", [], "2026-11-09T04:00:00.000Z")).workspace;
    expect(idea0(ws).history?.map((h) => h.action)).toEqual(["recorded", "corrected", "reopened"]);
    expect(idea0(ws).history?.[2]).toMatchObject({ status: "paper", outcomes: {}, voids: [] });
    expect(idea0(ws).outcomes).toEqual({});
    // It round-trips through the strict check.
    const back = ok(validateSlipWorkspace(JSON.parse(JSON.stringify(ws)))).workspace;
    expect(idea0(back).history?.length).toBe(3);
  });

  it("a refused settle leaves the history alone, and the store stamps entries from its own clock", async () => {
    const a = open(memory());
    await a.store.load();
    const sq = sampleQuote("jalen", "draftkings", "yes");
    await a.actions.addQuote(sq.quote, sq.market, 10);
    const saved = await a.actions.saveIdea({ id: "i1", status: "paper" });
    expect(saved.ok).toBe(true);
    const bad = await a.actions.recordOutcomes("i1", {}, [], "settled");
    expect(bad.ok).toBe(false);
    expect(a.store.get().workspace?.ideas[0]?.history).toBeUndefined();
    const mid = a.store.get().workspace?.ideas[0]?.legs[0]?.market.id ?? "";
    await a.actions.recordOutcomes("i1", { [mid]: "yes" }, [], "paper");
    expect(a.store.get().workspace?.ideas[0]?.history?.[0]?.action).toBe("recorded");
  });

  it("a version-1 workspace (no history) still loads, migrates, and a bad history is refused", () => {
    const v1 = JSON.parse(JSON.stringify(withIdea())) as { ideas: Record<string, unknown>[] };
    const m = ok(migrate(SLIP_CODEC, 1, v1));
    expect(((m.document as { ideas: { history: unknown[] }[] }).ideas[0] as { history: unknown[] }).history).toEqual([]);
    expect(SLIP_CODEC.validate(m.document).ok).toBe(true);
    expect(SLIP_CODEC.version).toBe(2);
    const bad = JSON.parse(JSON.stringify(withIdea())) as { ideas: Record<string, unknown>[] };
    bad.ideas[0] = { ...bad.ideas[0], history: [{ at: "not a time", action: "recorded", status: "paper", outcomes: {}, voids: [] }] };
    expect(validateSlipWorkspace(bad).ok).toBe(false);
  });

  it("an older workspace file imports, and an invalid import changes nothing", async () => {
    const mem = memory();
    const a = open(mem);
    await a.store.load();
    await a.actions.addPick(market(), "yes", "leg");
    const before = mem.map.get(storageKey({ kind: "device" }));
    const doc = JSON.parse(JSON.stringify(withIdea())) as Record<string, unknown>;
    const oldFile = JSON.stringify({ schema: "ph.betting.workspace", version: 1, document: doc });
    const badFile = JSON.stringify({ schema: "ph.betting.workspace", version: 2, document: { ...doc, ideas: [{ id: "x" }] } });
    expect((await a.store.importJson(badFile)).ok).toBe(false);
    expect(mem.map.get(storageKey({ kind: "device" }))).toBe(before);
    expect((await a.store.importJson(oldFile)).ok).toBe(true);
    expect(a.store.get().workspace?.ideas.length).toBe(1);
    const exported = await a.store.exportJson();
    expect(exported.ok && (JSON.parse(exported.json) as { version: number }).version).toBe(2);
  });
});

describe("Compare board ordering", () => {
  const board = { game_id: "g1", player_id: 7, stat: "pts" };
  it("a board is shown only for its own game, player and stat", async () => {
    const { boardMatches } = await import("./snapshots");
    const m = { eventId: "g1", playerId: "7", stat: "pts" } as const;
    expect(boardMatches(board, m)).toBe(true);
    expect(boardMatches(board, { ...m, playerId: "8" })).toBe(false);
    expect(boardMatches(board, { ...m, stat: "ast" })).toBe(false);
    expect(boardMatches(board, { ...m, eventId: "g2" })).toBe(false);
  });

  it("the loader hook drops an answer whose request was superseded, and the board hooks refuse a mismatched board", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const load = readFileSync(fileURLToPath(new URL("../useLoad.ts", import.meta.url)), "utf8");
    expect(load).toContain("let live = true");
    expect(load).toContain("live = false");
    expect(load.match(/if \(live\)/g)?.length).toBe(2);
    const boardSrc = readFileSync(fileURLToPath(new URL("./board.tsx", import.meta.url)), "utf8");
    expect(boardSrc.match(/boardMatches\(/g)?.length).toBe(2);
  });
});

describe("blocked storage", () => {
  const blocked = (): StorageLike => ({
    getItem: () => {
      throw new Error("SecurityError");
    },
    setItem: () => {
      throw new Error("SecurityError");
    },
  });

  it("the slip keeps working in this tab's memory, says so, and can still be exported", async () => {
    const { store, actions } = open(blocked());
    expect(await store.load()).toBe(true);
    expect(store.get().memoryOnly).toBe(true);
    expect(store.get().phase).toBe("ready");
    expect(store.get().workspace?.draft.legs).toEqual([]);
    const r = await actions.addPick(market(), "yes", "leg");
    expect(r.ok).toBe(true);
    expect(store.get().workspace?.picks.length).toBe(1);
    expect(store.get().dirty).toBe(false);
    const ex = await store.exportJson();
    expect(ex.ok && (JSON.parse(ex.json) as { document: { picks: unknown[] } }).document.picks.length).toBe(1);
  });

  it("a working store is never marked memory-only, and one that has read fine is never swapped for memory", async () => {
    const mem = memory();
    let broken = false;
    const flaky: StorageLike = {
      getItem: (k) => {
        if (broken) throw new Error("gone");
        return mem.getItem(k);
      },
      setItem: (k, v) => {
        mem.setItem(k, v);
      },
    };
    const { store, actions } = open(flaky);
    await store.load();
    expect(store.get().memoryOnly).toBe(false);
    await actions.addPick(market(), "yes", "leg");
    broken = true;
    const r = await actions.addPick(market(), "no", "leg");
    expect(r.ok).toBe(false);
    expect(store.get().memoryOnly).toBe(false);
  });

  it("the notice appears on every screen through the money note, and a build without it never ships it", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const parts = readFileSync(fileURLToPath(new URL("./parts.tsx", import.meta.url)), "utf8");
    expect(parts).toContain("<MemoryOnlyNote />");
    for (const screen of ["Home", "Slip", "Compare", "Hedge", "Ideas", "Analysis"]) {
      expect(readFileSync(fileURLToPath(new URL(`./${screen}.tsx`, import.meta.url)), "utf8"), screen).toContain("MoneyNote");
    }
  });
});
