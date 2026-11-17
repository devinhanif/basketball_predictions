// Ported from the donor kit's tests/integration/controller.test.ts (7 cases, on the session over a fake port), plus the
// local browser port: scope isolation, revision tags, idempotency keys, failed saves, invalid imports, migration.
import { describe, expect, it } from "vitest";
import {
  createSlipSession,
  createWorkspaceSession,
  LocalWorkspacePort,
  migrate,
  scopeForViewer,
  SLIP_CODEC,
  storageKey,
  type Codec,
  type PortResult,
  type StorageLike,
  type Versioned,
  type WorkspacePort,
} from "./repository";
import { buildNBASampleDraft, sampleWorkspace } from "./sample";
import { createSlipWorkspace } from "./slipModel";
import type { SlipWorkspace } from "./types";

// ------------------------------------------------------------------------------------------------ donor controller cases

interface Doc {
  picks: string[];
}
const copy = (v: Doc): Doc => JSON.parse(JSON.stringify(v)) as Doc;
const validate = (v: unknown): v is Doc => typeof v === "object" && v !== null && Array.isArray((v as Doc).picks) && (v as Doc).picks.every((x) => typeof x === "string");
function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve: (v: T) => void = () => undefined;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
type R = PortResult<Versioned<Doc>>;
const base = (): R => ({ ok: true, document: { picks: ["original"] }, revision: "r1" });
const notUsed = (): Promise<never> => Promise.reject(new Error("not used"));
function fakePort(p: { load: () => Promise<R>; save: (i: { document: Doc; expectedRevision: string | null; mutationKey: string }) => Promise<R> }): WorkspacePort<Doc> {
  return { load: p.load, save: p.save, exportJson: notUsed, importJson: notUsed };
}
function setup(port: WorkspacePort<Doc>) {
  let n = 0;
  return createWorkspaceSession({ port, validate, clone: copy, newMutationKey: () => `change-${++n}` });
}

describe("donor controller cases on the session", () => {
  it("a save is acknowledged asynchronously; duplicate saves and edits are blocked while pending", async () => {
    const gate = deferred<R>();
    let calls = 0;
    let received: { expectedRevision: string | null } | null = null;
    const c = setup(fakePort({ load: () => Promise.resolve(base()), save: (input) => { calls++; received = input; return gate.promise; } }));
    expect(await c.load()).toBe(true);
    expect(c.stage({ picks: ["bobby"] })).toBe(true);
    const saving = c.save();
    expect(c.getSnapshot().phase).toBe("saving");
    expect(c.getSnapshot().dirty).toBe(true);
    expect(c.stage({ picks: ["lost edit"] })).toBe(false);
    expect(await c.save()).toBe(false);
    expect(calls).toBe(1);
    expect(received).toMatchObject({ expectedRevision: "r1" });
    gate.resolve({ ok: true, document: { picks: ["bobby"] }, revision: "r2" });
    expect(await saving).toBe(true);
    expect(c.getSnapshot()).toMatchObject({ dirty: false, revision: "r2" });
  });

  it("a retry after a failure keeps the edit and the same idempotency key, and leaks no thrown text", async () => {
    const keys: string[] = [];
    const c = setup(fakePort({ load: () => Promise.resolve(base()), save: (i) => { keys.push(i.mutationKey); if (keys.length === 1) return Promise.reject(new Error("private error text")); return Promise.resolve({ ok: true, document: i.document, revision: "r2" }); } }));
    await c.load();
    c.stage({ picks: ["jalen", "bobby"] });
    expect(await c.save()).toBe(false);
    expect(c.getSnapshot().document?.picks).toEqual(["jalen", "bobby"]);
    expect(c.getSnapshot().dirty).toBe(true);
    expect(c.getSnapshot().error?.text.includes("private error text")).toBe(false);
    expect(await c.save()).toBe(true);
    expect(keys[0]).toBe(keys[1]);
  });

  it("a revision conflict keeps local edits; reload needs an explicit discard", async () => {
    const c = setup(fakePort({ load: () => Promise.resolve(base()), save: () => Promise.resolve({ ok: false, code: "conflict", text: "x" }) }));
    await c.load();
    c.stage({ picks: ["pending"] });
    expect(await c.save()).toBe(false);
    expect(c.getSnapshot().error?.code).toBe("conflict");
    expect(await c.load()).toBe(false);
    expect(c.getSnapshot().document?.picks).toEqual(["pending"]);
    expect(c.discard()).toBe(true);
    expect(c.getSnapshot().document?.picks).toEqual(["original"]);
    expect(c.getSnapshot().dirty).toBe(false);
  });

  it("an invalid load never substitutes a seeded demo or overwrites an acknowledged document", async () => {
    let invalid = false;
    const c = setup(fakePort({ load: () => Promise.resolve(invalid ? ({ ok: true, document: { picks: [99] }, revision: "r2" } as unknown as R) : base()), save: () => Promise.resolve(base()) }));
    await c.load();
    invalid = true;
    expect(await c.load()).toBe(false);
    expect(c.getSnapshot().error?.code).toBe("invalid_data");
    expect(c.getSnapshot().document?.picks).toEqual(["original"]);
  });

  it("an invalid acknowledgement leaves the edit dirty and never signals saved", async () => {
    const c = setup(fakePort({ load: () => Promise.resolve(base()), save: () => Promise.resolve({ ok: true, document: { picks: null }, revision: "r2" } as unknown as R) }));
    await c.load();
    c.stage({ picks: ["new"] });
    expect(await c.save()).toBe(false);
    expect(c.getSnapshot().dirty).toBe(true);
    expect(c.getSnapshot().revision).toBe("r1");
  });

  it("a superseded load cannot overwrite a newer snapshot", async () => {
    const old = deferred<R>();
    let calls = 0;
    const c = setup(fakePort({ load: () => (++calls === 1 ? old.promise : Promise.resolve({ ok: true, document: { picks: ["latest"] }, revision: "r2" })), save: () => Promise.resolve(base()) }));
    const first = c.load();
    const second = c.load();
    await second;
    old.resolve(base());
    expect(await first).toBe(false);
    expect(c.getSnapshot().document?.picks).toEqual(["latest"]);
  });

  it("cancel ignores a late save answer, and stage copies what it is given", async () => {
    const gate = deferred<R>();
    const c = setup(fakePort({ load: () => Promise.resolve(base()), save: () => gate.promise }));
    await c.load();
    const input = { picks: ["new"] };
    c.stage(input);
    input.picks.push("external mutation");
    expect(c.getSnapshot().document?.picks).toEqual(["new"]);
    const saving = c.save();
    c.cancel();
    gate.resolve({ ok: true, document: { picks: ["wrong"] }, revision: "r2" });
    expect(await saving).toBe(false);
    expect(c.getSnapshot().dirty).toBe(true);
    expect(c.getSnapshot().document?.picks).toEqual(["new"]);
  });
});

// ------------------------------------------------------------------------------------------------ local port

class MemoryStorage implements StorageLike {
  readonly map = new Map<string, string>();
  failWrites = false;
  failReads = false;
  writes = 0;
  getItem(key: string): string | null {
    if (this.failReads) throw new Error("SecurityError");
    return this.map.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.writes++;
    if (this.failWrites) throw new Error("QuotaExceededError");
    this.map.set(key, value);
  }
}

const NOW = "2026-11-17T05:00:00.000Z";
function port(storage: StorageLike, viewer: Parameters<typeof scopeForViewer>[0] = null) {
  const scope = scopeForViewer(viewer);
  if (scope === null) throw new Error("scope pending");
  let n = 0;
  return new LocalWorkspacePort<SlipWorkspace>({ storage, scope, codec: SLIP_CODEC, now: () => NOW, newRevision: () => `rev-${++n}` });
}
function unwrap<T extends object>(r: PortResult<T>): T {
  if (!r.ok) throw new Error(r.text);
  return r;
}
const withDraft = (title: string): SlipWorkspace => ({ ...createSlipWorkspace(), draft: { ...createSlipWorkspace().draft, title } });

describe("scope", () => {
  it("a platform viewer with an id is a user scope; anyone else is this device; a loading platform waits", () => {
    expect(scopeForViewer({ source: "platform", id: "u-1" })).toEqual({ kind: "user", userId: "u-1" });
    expect(scopeForViewer({ source: "local", id: null })).toEqual({ kind: "device" });
    expect(scopeForViewer({ source: "none", id: null })).toEqual({ kind: "device" });
    expect(scopeForViewer({ source: "platform", id: "  " })).toEqual({ kind: "device" });
    expect(scopeForViewer({ source: "none", id: null, loading: true })).toBeNull();
    expect(storageKey({ kind: "user", userId: "a/b" })).toBe("ph.betting.workspace:user:a%2Fb");
    expect(storageKey({ kind: "device" })).toBe("ph.betting.workspace:device");
  });

  it("one viewer never sees another viewer's (or this device's) data, even if the stored value is copied across keys", async () => {
    const s = new MemoryStorage();
    const alice = port(s, { source: "platform", id: "alice" });
    unwrap(await alice.save({ document: withDraft("alice's slip"), expectedRevision: null, mutationKey: "k1" }));
    const bob = port(s, { source: "platform", id: "bob" });
    const device = port(s);
    expect(unwrap(await bob.load())).toMatchObject({ revision: null, document: { draft: { title: "" } } });
    expect(unwrap(await device.load()).document.draft.title).toBe("");
    s.map.set(storageKey({ kind: "user", userId: "bob" }), s.map.get(storageKey({ kind: "user", userId: "alice" })) ?? "");
    expect(await bob.load()).toMatchObject({ ok: false, code: "scope_mismatch" });
    expect(unwrap(await alice.load()).document.draft.title).toBe("alice's slip");
  });
});

describe("local port", () => {
  it("round-trips a workspace with a revision tag; nothing stored loads as empty, never as the sample", async () => {
    const s = new MemoryStorage();
    const p = port(s);
    const first = unwrap(await p.load());
    expect(first.revision).toBeNull();
    expect(first.document.draft.legs.length).toBe(0);
    expect(first.document.draft.origin).toBe("user");
    expect(s.writes).toBe(0);
    const saved = unwrap(await p.save({ document: sampleWorkspace(), expectedRevision: null, mutationKey: "k1" }));
    expect(saved.revision).toBe("rev-1");
    const again = unwrap(await p.load());
    expect(again.revision).toBe("rev-1");
    expect(again.document.draft.legs[0]?.amount).toBe(10);
    expect(Object.isFrozen(again.document.draft.legs[0]?.quote)).toBe(true);
  });

  it("compare-and-set: a save based on an old revision is a conflict and changes nothing", async () => {
    const s = new MemoryStorage();
    const p = port(s);
    unwrap(await p.save({ document: withDraft("one"), expectedRevision: null, mutationKey: "k1" }));
    const before = s.map.get(storageKey({ kind: "device" }));
    expect(await p.save({ document: withDraft("two"), expectedRevision: null, mutationKey: "k2" })).toMatchObject({ ok: false, code: "conflict" });
    expect(s.map.get(storageKey({ kind: "device" }))).toBe(before);
  });

  it("the same idempotency key replays the earlier answer; reusing it for a different change is refused", async () => {
    const s = new MemoryStorage();
    const p = port(s);
    const a = unwrap(await p.save({ document: withDraft("one"), expectedRevision: null, mutationKey: "k1" }));
    const replay = unwrap(await p.save({ document: withDraft("one"), expectedRevision: null, mutationKey: "k1" }));
    expect(replay.revision).toBe(a.revision);
    expect(s.writes).toBe(1);
    expect(await p.save({ document: withDraft("other"), expectedRevision: a.revision, mutationKey: "k1" })).toMatchObject({ ok: false, code: "mutation_key_reused" });
  });

  it("a failed save (storage full) returns save_failed and keeps the stored data exactly as it was", async () => {
    const s = new MemoryStorage();
    const p = port(s);
    const a = unwrap(await p.save({ document: withDraft("kept"), expectedRevision: null, mutationKey: "k1" }));
    const before = s.map.get(storageKey({ kind: "device" }));
    s.failWrites = true;
    expect(await p.save({ document: withDraft("lost?"), expectedRevision: a.revision, mutationKey: "k2" })).toMatchObject({ ok: false, code: "save_failed" });
    expect(s.map.get(storageKey({ kind: "device" }))).toBe(before);
    s.failWrites = false;
    expect(unwrap(await p.load()).document.draft.title).toBe("kept");
  });

  it("an invalid document is not saved; blocked storage is a typed failure", async () => {
    const s = new MemoryStorage();
    const p = port(s);
    const bad = { ...createSlipWorkspace(), draft: { ...createSlipWorkspace().draft, tags: [17] } } as unknown as SlipWorkspace;
    expect(await p.save({ document: bad, expectedRevision: null, mutationKey: "k1" })).toMatchObject({ ok: false, code: "invalid_data" });
    expect(s.writes).toBe(0);
    s.failReads = true;
    expect(await p.load()).toMatchObject({ ok: false, code: "storage_unavailable" });
  });

  it("an unreadable stored value is reported and never overwritten by a save", async () => {
    const s = new MemoryStorage();
    s.map.set(storageKey({ kind: "device" }), "{not json");
    const p = port(s);
    expect(await p.load()).toMatchObject({ ok: false, code: "invalid_data" });
    expect(await p.save({ document: withDraft("x"), expectedRevision: null, mutationKey: "k1" })).toMatchObject({ ok: false, code: "invalid_data" });
    expect(s.map.get(storageKey({ kind: "device" }))).toBe("{not json");
  });

  it("export carries no viewer id; import validates and, when invalid, preserves current data", async () => {
    const s = new MemoryStorage();
    const alice = port(s, { source: "platform", id: "alice-id" });
    const a = unwrap(await alice.save({ document: withDraft("mine"), expectedRevision: null, mutationKey: "k1" }));
    const exp = unwrap(await alice.exportJson());
    expect(exp.json.includes("alice-id")).toBe(false);
    expect(exp.filename).toBe("parlayhitters-workspace-2026-11-17.json");
    const before = s.map.get(storageKey({ kind: "user", userId: "alice-id" }));
    const future = JSON.stringify({ ...(JSON.parse(exp.json) as object), version: 99 });
    const badDoc = JSON.stringify({ schema: "ph.betting.workspace", version: 1, document: { draft: {} } });
    for (const [text, code] of [["{oops", "invalid_data"], ['{"schema":"other"}', "invalid_data"], [future, "unsupported_version"], [badDoc, "invalid_data"], ["x".repeat(5_000_001), "import_too_large"]] as const) {
      const r = await alice.importJson({ text, expectedRevision: a.revision, mutationKey: `imp-${code}-${text.length}` });
      expect(r).toMatchObject({ ok: false, code });
      if (!r.ok) expect(r.text).toMatch(/Nothing was changed/);
      expect(s.map.get(storageKey({ kind: "user", userId: "alice-id" }))).toBe(before);
    }
    const bob = port(s, { source: "platform", id: "bob" });
    const imported = unwrap(await bob.importJson({ text: exp.json, expectedRevision: null, mutationKey: "imp-ok" }));
    expect(imported.document.draft.title).toBe("mine");
  });

  it("migration runs version steps in sequence and refuses newer or unreachable versions", () => {
    const codec: Codec<{ n: number }> = {
      schema: "t",
      version: 3,
      migrations: { 1: (d) => ({ n: (d as { n: number }).n + 1 }), 2: (d) => ({ n: (d as { n: number }).n * 10 }) },
      validate: (raw) => ({ ok: true, document: raw as { n: number } }),
      empty: () => ({ n: 0 }),
    };
    expect(migrate(codec, 1, { n: 1 })).toEqual({ ok: true, document: { n: 20 } });
    expect(migrate(codec, 3, { n: 1 })).toEqual({ ok: true, document: { n: 1 } });
    expect(migrate(codec, 4, { n: 1 })).toMatchObject({ ok: false, code: "unsupported_version" });
    expect(migrate(codec, "1", { n: 1 })).toMatchObject({ ok: false, code: "unsupported_version" });
    expect(migrate({ ...codec, migrations: { 1: codec.migrations[1] ?? ((d) => d) } }, 1, { n: 1 })).toMatchObject({ ok: false, code: "unsupported_version" });
  });
});

describe("sample only on explicit action", () => {
  it("loading, saving and failing never seed the sample; the sample function labels every record it makes", async () => {
    const s = new MemoryStorage();
    const session = createSlipSession(port(s), () => "k-1");
    expect(await session.load()).toBe(true);
    expect(session.getSnapshot().document?.draft.legs.length).toBe(0);
    s.failReads = true;
    expect(await session.load()).toBe(false);
    expect(session.getSnapshot().document?.draft.legs.length).toBe(0);
    s.failReads = false;
    const sample = sampleWorkspace();
    expect(sample.draft.origin).toBe("sample");
    expect(sample.draft.legs.every((l) => l.quote.provenance === "sample" && l.market.origin === "sample")).toBe(true);
    expect(buildNBASampleDraft().legs.every((l) => l.market.eventDate < "2025-07-01")).toBe(true);
    expect(session.stage(sample)).toBe(true);
    expect(await session.save()).toBe(true);
    expect(session.getSnapshot()).toMatchObject({ dirty: false, revision: "rev-1" });
  });
});
