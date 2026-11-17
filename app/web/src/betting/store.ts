// The one betting workspace store: the viewer's slip, ideas, typed prices and unpriced picks, loaded and saved through
// LocalWorkspacePort under the viewer's scope. Edits run one at a time (a quick second edit waits for the first save
// instead of being dropped). A failed save keeps the edit on screen, marked unsaved, with retry and discard; an invalid
// import changes nothing. No network: every action here writes to the injected browser storage and nowhere else.
import { addPick, addQuote, addRecordedLeg, loadIdea, markPlaced, putManualQuote, recordOutcomes, removeIdea, removeLeg, removePick, replacePlannedQuote, saveIdea, setEntry, setLegAmount, setNotes, settlePicks, toggleLeg } from "./slipModel";
import { mergeIdeasFile } from "./ideaModel";
import { createSlipSession, LocalWorkspacePort, scopeId, storageKey, SLIP_CODEC, type RepoCode, type StorageLike, type WorkspaceScope, type WorkspaceSession } from "./repository";
import { sampleWorkspace } from "./sample";
import type { EntryTerms, IdeaStatus, Market, Quote, Side, SlipDraft, SlipWorkspace } from "./types";
import { localId } from "./ids";

export interface BetView {
  readonly phase: "idle" | "loading" | "ready" | "saving" | "error";
  readonly workspace: SlipWorkspace | null;
  /** True while an edit is on screen that the browser has not stored (a failed save). */
  readonly dirty: boolean;
  readonly error: { readonly code: RepoCode | "not_loaded"; readonly text: string } | null;
  /**
   * True when the browser would not let the page read its storage at all, so the slip lives in this tab's memory only: it
   * works, but is gone when the tab closes or reloads (export keeps it).
   */
  readonly memoryOnly: boolean;
}

export type Done = { readonly ok: true; readonly text: string } | { readonly ok: false; readonly code: string; readonly text: string };

type Step = { ok: true; workspace: SlipWorkspace; text?: string } | { ok: false; code: string; text: string };

export interface BetStoreDeps {
  readonly storage: StorageLike;
  readonly scope: WorkspaceScope;
  /** ISO instant now (the store never reads a clock itself). */
  readonly nowIso: () => string;
}

export interface BetStore {
  readonly scope: WorkspaceScope;
  get(): BetView;
  subscribe(listener: () => void): () => void;
  load(opts?: { discardLocal?: boolean }): Promise<boolean>;
  /** Runs one edit on the newest workspace and saves it. */
  edit(fn: (ws: SlipWorkspace) => Step): Promise<Done>;
  /**
   * Another tab of this browser wrote storage: re-read this viewer's workspace. Only this scope's key counts (a null key,
   * as when storage was cleared, counts too). It waits behind any edit or save in flight, and an unsaved edit on screen is
   * never overwritten (a later save then reports the conflict). Returns whether a newer copy was taken.
   */
  externalChange(key: string | null): Promise<boolean>;
  retry(): Promise<Done>;
  discard(): void;
  exportJson(): Promise<{ ok: true; json: string; filename: string } | { ok: false; code: string; text: string }>;
  importJson(text: string): Promise<Done>;
}

export function createBetStore(deps: BetStoreDeps): BetStore {
  // A browser that refuses the very first read (blocked storage, some private modes) gets this tab's memory instead of a dead
  // slip. It is never silent: the view says memoryOnly and every screen says so. A store that has read fine once is never
  // swapped for memory (that would hide what is saved).
  const mem = new Map<string, string>();
  let memoryOnly = false;
  let readOnce = false;
  const storage: StorageLike = {
    getItem(k) {
      if (memoryOnly) return mem.get(k) ?? null;
      try {
        const v = deps.storage.getItem(k);
        readOnce = true;
        return v;
      } catch (e) {
        if (readOnce) throw e;
        memoryOnly = true;
        return mem.get(k) ?? null;
      }
    },
    setItem(k, v) {
      if (memoryOnly) mem.set(k, v);
      else deps.storage.setItem(k, v);
    },
  };
  const port = new LocalWorkspacePort<SlipWorkspace>({ storage, scope: deps.scope, codec: SLIP_CODEC, now: deps.nowIso });
  const session: WorkspaceSession<SlipWorkspace> = createSlipSession(port);
  let queue: Promise<unknown> = Promise.resolve();
  const view = (): BetView => {
    const s = session.getSnapshot();
    return { phase: s.phase, workspace: s.document, dirty: s.dirty, error: s.error, memoryOnly };
  };
  let snapshot = view();
  const listeners = new Set<() => void>();
  session.subscribe(() => {
    snapshot = view();
    for (const l of listeners) l();
  });
  const serial = <T>(run: () => Promise<T>): Promise<T> => {
    const p = queue.then(run);
    queue = p.then(
      () => undefined,
      () => undefined,
    );
    return p;
  };

  return {
    scope: deps.scope,
    get: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load: (opts) => serial(() => session.load(opts)),
    edit(fn) {
      return serial(async (): Promise<Done> => {
        const doc = session.getSnapshot().document;
        if (doc === null) return { ok: false, code: "not_loaded", text: "The slip has not loaded, so nothing was changed." };
        const step = fn(doc);
        if (!step.ok) return step;
        if (step.workspace === doc) return { ok: true, text: step.text ?? "" };
        if (!session.stage(step.workspace)) {
          return { ok: false, code: "invalid_data", text: session.getSnapshot().error?.text ?? "That change could not be applied." };
        }
        if (await session.save()) return { ok: true, text: step.text ?? "" };
        return { ok: false, code: session.getSnapshot().error?.code ?? "save_failed", text: session.getSnapshot().error?.text ?? "Not saved." };
      });
    },
    externalChange(key) {
      if (key !== null && key !== storageKey(deps.scope)) return Promise.resolve(false);
      return serial(async () => {
        const s = session.getSnapshot();
        if (s.dirty || s.document === null) return false;
        return session.load();
      });
    },
    retry() {
      return serial(async (): Promise<Done> => {
        if (await session.save()) return { ok: true, text: "Saved." };
        return { ok: false, code: session.getSnapshot().error?.code ?? "save_failed", text: session.getSnapshot().error?.text ?? "Not saved." };
      });
    },
    discard() {
      session.discard();
    },
    async exportJson() {
      const r = await port.exportJson();
      return r.ok ? { ok: true, json: r.json, filename: r.filename } : { ok: false, code: r.code, text: r.text };
    },
    importJson(text) {
      return serial(async (): Promise<Done> => {
        const s = session.getSnapshot();
        if (s.dirty) return { ok: false, code: "unsaved_changes", text: "Save or discard the unsaved change first. Nothing was imported." };
        const r = await port.importJson({ text, expectedRevision: s.revision, mutationKey: localId("import") });
        if (!r.ok) return { ok: false, code: r.code, text: r.text };
        await session.load();
        return { ok: true, text: "Imported. The workspace in this browser now matches the file." };
      });
    },
  };
}

/** A short name for where the workspace lives: "this device" or the platform account. */
export function scopeLabel(scope: WorkspaceScope): string {
  return scope.kind === "device" ? "this device" : `your account (${scopeId(scope).slice(5, 25)})`;
}

// ------------------------------------------------------------------------------------------------ actions

const fail = (r: { code: string; text: string }): Step => ({ ok: false, code: r.code, text: r.text });
const withDraft = (ws: SlipWorkspace, draft: SlipDraft, text?: string): Step => ({ ok: true, workspace: { ...ws, draft }, ...(text !== undefined ? { text } : {}) });

/** The actions the screens call. Each is one edit; none sends anything anywhere. */
export function betActions(store: BetStore, nowIso: () => string) {
  return {
    addPick: (market: Market, side: Side, from: string) =>
      store.edit((ws) => {
        const r = addPick(ws, market, side, { from, now: nowIso() });
        if (!r.ok) return fail(r);
        const said = r.outcome === "added" ? "Added to your slip. Choose a venue price in Compare, or type one." : r.outcome === "reenabled" ? "Switched back on in your slip (it was already there)." : "Already in your slip.";
        return { ok: true, workspace: r.workspace, text: said };
      }),
    removePick: (pickId: string) => store.edit((ws) => ({ ok: true, workspace: removePick(ws, pickId), text: "Removed from your slip." })),
    /** A planned leg at this venue price; a pick waiting on the same market and side is now priced and leaves the list. */
    addQuote: (quote: Quote, market: Market, amount?: number | null) =>
      store.edit((ws) => {
        const r = addQuote(ws.draft, quote, market, amount === undefined ? {} : { amount });
        if (!r.ok) return fail(r);
        return { ok: true, workspace: settlePicks({ ...ws, draft: r.draft }, market.id, quote.side), text: "Added to your slip." };
      }),
    addRecorded: (quote: Quote, market: Market, amount: number) =>
      store.edit((ws) => {
        const r = addRecordedLeg(ws.draft, quote, market, amount, nowIso());
        return r.ok ? withDraft(ws, r.draft, "Recorded in this browser as a position you already hold. Nothing was sent anywhere.") : fail(r);
      }),
    /** Keeps a price the viewer typed for one venue, market and side (replacing an earlier one there). Legs are untouched. */
    putTyped: (quote: Quote) =>
      store.edit((ws) => {
        const r = putManualQuote(ws, quote);
        return r.ok ? { ok: true, workspace: r.workspace, text: "Price kept as your entry (manual). Slip legs keep the price they were added at." } : fail(r);
      }),
    toggle: (legId: string) =>
      store.edit((ws) => {
        const r = toggleLeg(ws.draft, legId);
        return r.ok ? withDraft(ws, r.draft) : fail(r);
      }),
    remove: (legId: string) => store.edit((ws) => withDraft(ws, removeLeg(ws.draft, legId), "Removed from this browser's slip.")),
    setAmount: (legId: string, amount: number) =>
      store.edit((ws) => {
        const r = setLegAmount(ws.draft, legId, amount);
        return r.ok ? withDraft(ws, r.draft) : fail(r);
      }),
    setEntry: (entry: EntryTerms) => store.edit((ws) => withDraft(ws, setEntry(ws.draft, entry))),
    replaceQuote: (legId: string, quote: Quote) =>
      store.edit((ws) => {
        const r = replacePlannedQuote(ws.draft, legId, quote);
        return r.ok ? withDraft(ws, r.draft, "Price updated (your entry).") : fail(r);
      }),
    setNotes: (notes: { title?: string; thesis?: string; tags?: readonly string[] }) => store.edit((ws) => withDraft(ws, setNotes(ws.draft, notes))),
    /** Records the switched-on planned legs as placed elsewhere. A local note only: nothing leaves this browser. */
    markPlaced: () =>
      store.edit((ws) => {
        const n = ws.draft.legs.filter((l) => l.role === "planned" && l.enabled).length;
        if (n === 0) return { ok: false, code: "nothing_selected", text: "Switch on at least one planned leg first." };
        return withDraft(ws, markPlaced(ws.draft, nowIso()), `${String(n)} ${n === 1 ? "leg" : "legs"} recorded as placed in this browser. Nothing was sent anywhere.`);
      }),
    /** Replaces the working slip with the labelled demo (ideas, typed prices and picks stay). */
    loadDemo: () => store.edit((ws) => withDraft(ws, sampleWorkspace().draft, "Loaded the labelled demo. Every price in it is an invented sample.")),
    clearDraft: () => store.edit((ws) => withDraft(ws, { ...ws.draft, legs: [] }, "The working slip is empty. Saved ideas are unchanged.")),
    /** Saves the working slip as an idea: an independent frozen snapshot of every leg and price, taken now. */
    saveIdea: (opts: { id: string; status: "watching" | "paper"; title?: string }) =>
      store.edit((ws) => {
        const r = saveIdea(ws, { now: nowIso(), status: opts.status, id: opts.id, ...(opts.title !== undefined ? { title: opts.title } : {}) });
        return r.ok ? { ok: true, workspace: r.workspace, text: "Saved as an idea. Its prices are frozen as they are now; later price changes never reach it." } : fail(r);
      }),
    /** Replaces the working slip with a copy of the idea (the idea itself is unchanged). */
    loadIdea: (ideaId: string) =>
      store.edit((ws) => {
        const r = loadIdea(ws, ideaId);
        return r.ok ? { ok: true, workspace: r.workspace, text: "A copy of the idea is now your working slip. The saved idea is unchanged." } : fail(r);
      }),
    removeIdea: (ideaId: string) =>
      store.edit((ws) => {
        const r = removeIdea(ws, ideaId);
        return r.ok ? { ok: true, workspace: r.workspace, text: "Idea deleted from this browser." } : fail(r);
      }),
    /** The viewer's own paper outcomes (never a venue's settlement); "settled" needs every market. */
    recordOutcomes: (ideaId: string, outcomes: Readonly<Record<string, Side>>, voids: readonly string[], status: IdeaStatus) =>
      store.edit((ws) => {
        const r = recordOutcomes(ws, ideaId, outcomes, status, voids, nowIso());
        return r.ok ? { ok: true, workspace: r.workspace, text: status === "settled" ? "Paper result recorded from the outcomes you entered." : "Saved. The idea stays open." } : fail(r);
      }),
    /** Adds the ideas from an ideas file; an invalid file changes nothing and says why. */
    importIdeas: (text: string) =>
      store.edit((ws) => {
        const r = mergeIdeasFile(ws, text);
        if (!r.ok) return { ok: false, code: r.code, text: r.text };
        return { ok: true, workspace: r.workspace, text: `${String(r.added)} ${r.added === 1 ? "idea" : "ideas"} added${r.skipped ? `; ${String(r.skipped)} already here and left as they were` : ""}.` };
      }),
  };
}

export type BetActions = ReturnType<typeof betActions>;
