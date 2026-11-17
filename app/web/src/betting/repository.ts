// Where the betting workspace lives: the viewer's own browser, under a key scoped to who is looking, as a versioned
// document with a revision tag (compare-and-set) and an idempotency key per mutation. A failed save or an invalid import
// leaves the stored data exactly as it was and returns a typed failure. Nothing is ever seeded: an empty or unreadable
// store is reported as such, never filled with the sample. No network: the only port built here is local.
import { isObject, isInstant } from "./catalog";
import { digest, jsonCopy, localId } from "./ids";
import { createSlipWorkspace, validateSlipWorkspace } from "./slipModel";
import type { Failure, SlipWorkspace } from "./types";

// ------------------------------------------------------------------------------------------------ scope

/** Whose workspace this is: a signed-in platform viewer, or this device when the viewer is not known. */
export type WorkspaceScope = { readonly kind: "user"; readonly userId: string } | { readonly kind: "device" };

/** The parts of the app's `Identity` (src/identity.ts) this needs; passed in, never read here. */
export interface ViewerRef {
  readonly source: "platform" | "local" | "none";
  readonly id: string | null;
  readonly loading?: boolean;
}

/**
 * The scope for a viewer: a platform viewer with an id gets their own scope; everyone else gets the explicit "this device"
 * scope (a typed display name is not an identity). While the platform has not answered yet, null: wait, do not guess.
 */
export function scopeForViewer(viewer: ViewerRef | null): WorkspaceScope | null {
  if (viewer === null) return { kind: "device" };
  if (viewer.loading === true) return null;
  if (viewer.source === "platform" && typeof viewer.id === "string" && viewer.id.trim() !== "") return { kind: "user", userId: viewer.id };
  return { kind: "device" };
}

export const KEY_PREFIX = "ph.betting.workspace";

export function scopeId(scope: WorkspaceScope): string {
  return scope.kind === "user" ? `user:${encodeURIComponent(scope.userId)}` : "device";
}

/** The browser storage key for one scope. Different scopes never share a key. */
export function storageKey(scope: WorkspaceScope): string {
  return `${KEY_PREFIX}:${scopeId(scope)}`;
}

// ------------------------------------------------------------------------------------------------ the port

export type RepoCode =
  | "invalid_data"
  | "unsupported_version"
  | "scope_mismatch"
  | "conflict"
  | "save_failed"
  | "load_failed"
  | "storage_unavailable"
  | "mutation_key_reused"
  | "import_too_large"
  | "unsaved_changes"
  | "busy";

export type RepoFailure = Failure<RepoCode>;

/** A document with its revision tag; `revision` is null when nothing has been saved in this scope yet. */
export interface Versioned<T> {
  readonly document: T;
  readonly revision: string | null;
}

export type PortResult<T> = ({ readonly ok: true } & T) | RepoFailure;

/**
 * Load, save with a revision tag, export, import. Every call resolves to a typed result; none throws for an expected
 * failure. A save names the revision it was based on (`expectedRevision`) and carries an idempotency key: replaying the
 * same key with the same document returns the earlier acknowledgement; the same key with a different document is refused.
 */
export interface WorkspacePort<T> {
  load(): Promise<PortResult<Versioned<T>>>;
  save(input: { document: T; expectedRevision: string | null; mutationKey: string }): Promise<PortResult<Versioned<T>>>;
  exportJson(): Promise<PortResult<{ json: string; filename: string }>>;
  importJson(input: { text: string; expectedRevision: string | null; mutationKey: string }): Promise<PortResult<Versioned<T>>>;
}

// SEAM (not built): a future server-backed WorkspacePort would live beside LocalWorkspacePort and implement the same four
// methods against an authenticated, user-scoped store that checks `expectedRevision` atomically and deduplicates
// `mutationKey` per user. It is Devin's decision whether to ever add one; the app's API is read-only today.

/** Stored and exported shapes are versioned; `migrations[n]` turns version n into version n + 1. */
export interface Codec<T> {
  readonly schema: string;
  readonly version: number;
  readonly migrations: Readonly<Record<number, (doc: unknown) => unknown>>;
  validate(raw: unknown): { ok: true; document: T } | { ok: false; text: string };
  empty(): T;
}

export const SLIP_SCHEMA = "ph.betting.workspace";
export const SLIP_VERSION = 1;

export const SLIP_CODEC: Codec<SlipWorkspace> = {
  schema: SLIP_SCHEMA,
  version: SLIP_VERSION,
  migrations: {},
  validate(raw) {
    const r = validateSlipWorkspace(raw);
    return r.ok ? { ok: true, document: r.workspace } : { ok: false, text: r.text };
  },
  empty: createSlipWorkspace,
};

/** Runs a document from `from` up to `codec.version`; a newer or unreachable version is refused, never guessed. */
export function migrate<T>(codec: Codec<T>, from: unknown, doc: unknown): { ok: true; document: unknown } | RepoFailure {
  if (typeof from !== "number" || !Number.isInteger(from) || from < 1) return { ok: false, code: "unsupported_version", text: "The document has no readable version." };
  if (from > codec.version) return { ok: false, code: "unsupported_version", text: `The document is version ${from}; this app reads up to version ${codec.version}.` };
  let out = doc;
  for (let v = from; v < codec.version; v++) {
    const step = codec.migrations[v];
    if (step === undefined) return { ok: false, code: "unsupported_version", text: `No migration from version ${v}.` };
    out = step(out);
  }
  return { ok: true, document: out };
}

/** The part of the Web Storage API this uses, so tests can pass an in-memory one. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface Envelope {
  schema: string;
  version: number;
  scope: string;
  revision: string;
  savedAt: string;
  lastMutation: { key: string; digest: string } | null;
  document: unknown;
}

export const MAX_IMPORT_CHARS = 5_000_000;

export interface LocalPortOptions<T> {
  readonly storage: StorageLike;
  readonly scope: WorkspaceScope;
  readonly codec: Codec<T>;
  /** ISO instant for `savedAt` / export names; injected so tests and callers own the clock. */
  readonly now: () => string;
  readonly newRevision?: () => string;
}

/** The one WorkspacePort built here: the viewer's browser storage under a scope-specific key. */
export class LocalWorkspacePort<T> implements WorkspacePort<T> {
  private readonly key: string;
  private readonly scopeTag: string;
  private readonly newRevision: () => string;

  constructor(private readonly opts: LocalPortOptions<T>) {
    this.key = storageKey(opts.scope);
    this.scopeTag = scopeId(opts.scope);
    this.newRevision = opts.newRevision ?? ((): string => localId("rev"));
  }

  private readEnvelope(): { ok: true; envelope: Envelope | null } | RepoFailure {
    let raw: string | null;
    try {
      raw = this.opts.storage.getItem(this.key);
    } catch {
      return { ok: false, code: "storage_unavailable", text: "This browser is not letting the page read its saved data (private window or blocked storage)." };
    }
    if (raw === null) return { ok: true, envelope: null };
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { ok: false, code: "invalid_data", text: "The saved workspace is not readable. It was left as it is; nothing was replaced." };
    }
    if (!isObject(parsed) || parsed["schema"] !== this.opts.codec.schema || typeof parsed["revision"] !== "string" || !isInstant(parsed["savedAt"])) {
      return { ok: false, code: "invalid_data", text: "The saved workspace is not in a shape this app reads. It was left as it is." };
    }
    if (parsed["scope"] !== this.scopeTag) return { ok: false, code: "scope_mismatch", text: "The saved workspace belongs to a different viewer. It was not loaded." };
    const lm = parsed["lastMutation"];
    const lastMutation = isObject(lm) && typeof lm["key"] === "string" && typeof lm["digest"] === "string" ? { key: lm["key"], digest: lm["digest"] } : null;
    return {
      ok: true,
      envelope: { schema: parsed["schema"], version: parsed["version"] as number, scope: parsed["scope"], revision: parsed["revision"], savedAt: parsed["savedAt"], lastMutation, document: parsed["document"] },
    };
  }

  private decode(env: Envelope): { ok: true; document: T } | RepoFailure {
    const m = migrate(this.opts.codec, env.version, env.document);
    if (!m.ok) return m;
    const v = this.opts.codec.validate(m.document);
    return v.ok ? v : { ok: false, code: "invalid_data", text: `The saved workspace failed its check (${v.text}). It was left as it is.` };
  }

  load(): Promise<PortResult<Versioned<T>>> {
    const r = this.readEnvelope();
    if (!r.ok) return Promise.resolve(r);
    if (r.envelope === null) return Promise.resolve({ ok: true, document: this.opts.codec.empty(), revision: null });
    const d = this.decode(r.envelope);
    return Promise.resolve(d.ok ? { ok: true, document: d.document, revision: r.envelope.revision } : d);
  }

  save(input: { document: T; expectedRevision: string | null; mutationKey: string }): Promise<PortResult<Versioned<T>>> {
    return Promise.resolve(this.saveNow(input.document, input.expectedRevision, input.mutationKey));
  }

  private saveNow(document: unknown, expectedRevision: string | null, mutationKey: string): PortResult<Versioned<T>> {
    if (typeof mutationKey !== "string" || mutationKey.trim() === "") return { ok: false, code: "save_failed", text: "A save needs an idempotency key." };
    const v = this.opts.codec.validate(document);
    if (!v.ok) return { ok: false, code: "invalid_data", text: `Not saved, because the workspace failed its check (${v.text}). Your saved data is unchanged.` };
    const body = JSON.stringify(v.document);
    const sum = digest(body);
    const cur = this.readEnvelope();
    // A store that cannot be read is never overwritten: that would destroy what is there.
    if (!cur.ok) return cur.code === "storage_unavailable" ? cur : { ok: false, code: cur.code, text: `${cur.text} Not saved over it.` };
    const env = cur.envelope;
    if (env?.lastMutation?.key === mutationKey) {
      if (env.lastMutation.digest !== sum) return { ok: false, code: "mutation_key_reused", text: "This change's key was already used for a different change." };
      const d = this.decode(env);
      return d.ok ? { ok: true, document: d.document, revision: env.revision } : d;
    }
    const currentRevision = env?.revision ?? null;
    if (currentRevision !== expectedRevision) return { ok: false, code: "conflict", text: "The workspace changed since you opened it (another tab?). Export your edits, reload, and reconcile." };
    const next: Envelope = { schema: this.opts.codec.schema, version: this.opts.codec.version, scope: this.scopeTag, revision: this.newRevision(), savedAt: this.opts.now(), lastMutation: { key: mutationKey, digest: sum }, document: JSON.parse(body) as unknown };
    try {
      this.opts.storage.setItem(this.key, JSON.stringify(next));
    } catch {
      return { ok: false, code: "save_failed", text: "Not saved: the browser refused to store it (full or blocked). Your earlier saved data is unchanged; your edits are still here to retry." };
    }
    return { ok: true, document: v.document, revision: next.revision };
  }

  exportJson(): Promise<PortResult<{ json: string; filename: string }>> {
    const r = this.readEnvelope();
    if (!r.ok) return Promise.resolve(r);
    let document: unknown = this.opts.codec.empty();
    if (r.envelope !== null) {
      const d = this.decode(r.envelope);
      if (!d.ok) return Promise.resolve(d);
      document = d.document;
    }
    const exportedAt = this.opts.now();
    // The export carries no scope or viewer id: it is the viewer's own file to keep or move.
    const json = JSON.stringify({ schema: this.opts.codec.schema, version: this.opts.codec.version, exportedAt, document }, null, 2);
    return Promise.resolve({ ok: true, json, filename: `parlayhitters-workspace-${exportedAt.slice(0, 10)}.json` });
  }

  importJson(input: { text: string; expectedRevision: string | null; mutationKey: string }): Promise<PortResult<Versioned<T>>> {
    return Promise.resolve(this.importNow(input.text, input.expectedRevision, input.mutationKey));
  }

  private importNow(text: string, expectedRevision: string | null, mutationKey: string): PortResult<Versioned<T>> {
    if (typeof text !== "string" || text.length > MAX_IMPORT_CHARS) return { ok: false, code: "import_too_large", text: "That file is too large to import. Nothing was changed." };
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      return { ok: false, code: "invalid_data", text: "That file is not JSON. Nothing was changed." };
    }
    if (!isObject(parsed) || parsed["schema"] !== this.opts.codec.schema) return { ok: false, code: "invalid_data", text: "That file is not a parlayhitters workspace export. Nothing was changed." };
    const m = migrate(this.opts.codec, parsed["version"], parsed["document"]);
    if (!m.ok) return { ...m, text: `${m.text} Nothing was changed.` };
    const v = this.opts.codec.validate(m.document);
    if (!v.ok) return { ok: false, code: "invalid_data", text: `That file failed its check (${v.text}). Nothing was changed.` };
    return this.saveNow(v.document, expectedRevision, mutationKey);
  }
}

// ------------------------------------------------------------------------------------------------ session (controller)

export type SessionPhase = "idle" | "loading" | "ready" | "saving" | "error";

export interface SessionState<T> {
  readonly phase: SessionPhase;
  readonly document: T | null;
  readonly revision: string | null;
  /** True while there is a local edit the store has not acknowledged. */
  readonly dirty: boolean;
  readonly error: { readonly code: RepoCode; readonly text: string } | null;
}

export interface SessionOptions<T> {
  readonly port: WorkspacePort<T>;
  readonly validate: (value: unknown) => value is T;
  readonly clone: (value: T) => T;
  readonly newMutationKey?: () => string;
}

export interface WorkspaceSession<T> {
  getSnapshot(): SessionState<T>;
  subscribe(listener: () => void): () => void;
  load(opts?: { discardLocal?: boolean }): Promise<boolean>;
  /** Accepts a LOCAL edit only (true = staged here, not saved). */
  stage(next: T): boolean;
  save(): Promise<boolean>;
  /** Back to the last acknowledged document. */
  discard(): boolean;
  /** Ignore any in-flight answer (scope change, unmount). */
  cancel(): void;
}

/**
 * Local edits and their acknowledgement. A failed save keeps the edit (dirty) and the same idempotency key for the retry;
 * a conflict keeps the edit and refuses to reload over it until it is discarded; an invalid answer never replaces what is
 * shown; a superseded or cancelled answer is ignored. Never substitutes sample data.
 */
export function createWorkspaceSession<T>(opts: SessionOptions<T>): WorkspaceSession<T> {
  const newKey = opts.newMutationKey ?? ((): string => localId("mut"));
  let state: SessionState<T> = { phase: "idle", document: null, revision: null, dirty: false, error: null };
  let baseline: T | null = null;
  let opSeq = 0;
  let pendingKey: string | null = null;
  const listeners = new Set<() => void>();
  const emit = (next: SessionState<T>): void => {
    state = next;
    for (const l of listeners) l();
  };
  const fail = (code: RepoCode, text: string): void => {
    emit({ ...state, phase: "error", error: { code, text } });
  };
  const accept = (doc: T, revision: string | null): void => {
    baseline = opts.clone(doc);
    pendingKey = null;
    emit({ phase: "ready", document: opts.clone(doc), revision, dirty: false, error: null });
  };

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    async load({ discardLocal = false } = {}) {
      if (state.phase === "saving") return false;
      if (state.dirty && !discardLocal) {
        emit({ ...state, error: { code: "unsaved_changes", text: "Save or discard your local changes before reloading." } });
        return false;
      }
      const op = ++opSeq;
      emit({ ...state, phase: "loading", error: null });
      let r: PortResult<Versioned<T>>;
      try {
        r = await opts.port.load();
      } catch {
        if (op !== opSeq) return false;
        fail("load_failed", "Could not load this workspace. Anything already shown was kept.");
        return false;
      }
      if (op !== opSeq) return false;
      if (!r.ok) {
        fail(r.code, r.text);
        return false;
      }
      if (!opts.validate(r.document)) {
        fail("invalid_data", "The store answered with a document this app does not read. Nothing was substituted.");
        return false;
      }
      accept(r.document, r.revision);
      return true;
    },
    stage(next) {
      if (state.document === null || state.phase === "loading" || state.phase === "saving") return false;
      if (!opts.validate(next)) {
        emit({ ...state, error: { code: "invalid_data", text: "That edit is not valid. Your previous draft was kept." } });
        return false;
      }
      pendingKey = newKey();
      emit({ ...state, phase: "ready", document: opts.clone(next), dirty: true, error: null });
      return true;
    },
    async save() {
      if (state.phase === "saving" || state.phase === "loading" || state.document === null) return false;
      if (!state.dirty) return true;
      const op = ++opSeq;
      const document = opts.clone(state.document);
      const expectedRevision = state.revision;
      pendingKey ??= newKey();
      const mutationKey = pendingKey;
      emit({ ...state, phase: "saving", error: null });
      let r: PortResult<Versioned<T>>;
      try {
        r = await opts.port.save({ document, expectedRevision, mutationKey });
      } catch {
        if (op !== opSeq) return false;
        fail("save_failed", "Not saved. Your edits are still here; retrying uses the same change key.");
        return false;
      }
      if (op !== opSeq) return false;
      if (!r.ok) {
        fail(r.code, r.code === "conflict" ? "A newer saved version exists. Export your edits, then discard and reload to reconcile." : r.text);
        return false;
      }
      if (!opts.validate(r.document)) {
        fail("invalid_data", "The save answer was not valid. Your edits are still pending; retry uses the same change key.");
        return false;
      }
      accept(r.document, r.revision);
      return true;
    },
    discard() {
      if (state.phase === "saving" || state.phase === "loading" || baseline === null) return false;
      pendingKey = null;
      emit({ ...state, phase: "ready", document: opts.clone(baseline), dirty: false, error: null });
      return true;
    },
    cancel() {
      opSeq++;
      if (state.phase === "loading" || state.phase === "saving") emit({ ...state, phase: state.document !== null ? "ready" : "idle" });
    },
  };
}

/** A session over the slip workspace with the standard validation and copy. */
export function createSlipSession(port: WorkspacePort<SlipWorkspace>, newMutationKey?: () => string): WorkspaceSession<SlipWorkspace> {
  return createWorkspaceSession({
    port,
    validate: (v): v is SlipWorkspace => validateSlipWorkspace(v).ok,
    // A validated copy is deep-frozen, so the document a screen holds cannot be edited in place.
    clone: (v) => {
      const r = validateSlipWorkspace(v);
      return r.ok ? r.workspace : jsonCopy(v);
    },
    ...(newMutationKey ? { newMutationKey } : {}),
  });
}
