// The browser side of the betting workspace: one store per viewer scope (kept for the page's life, so the slip survives
// route changes), the scoped stylesheet, the clock tick, and the slip panel's open state. The store itself is pure
// (store.ts); this file hands it the browser's storage and clock.
import { useEffect, useState } from "preact/hooks";
import { useIdentity } from "../identity";
import { readKey, writeKey } from "../prefs";
import { pushToast } from "../toast";
import BET_CSS from "./betting.css?inline";
import { scopeForViewer, scopeId, storageKey, type StorageLike, type WorkspaceScope } from "./repository";
import { betActions, createBetStore, type BetActions, type BetStore, type BetView, type Done } from "./store";

/** Puts the betting stylesheet in the page once. It ships inside the betting module, so a build without it has none. */
export function ensureBetStyles(): void {
  if (typeof document === "undefined" || document.getElementById("ph-bet-css")) return;
  const el = document.createElement("style");
  el.id = "ph-bet-css";
  el.textContent = BET_CSS;
  document.head.appendChild(el);
}

/** The browser's storage, or one that refuses (private mode, blocked storage): the port then reports it plainly. */
function browserStorage(): StorageLike {
  try {
    const s = window.localStorage;
    return {
      getItem: (k) => s.getItem(k),
      setItem: (k, v) => {
        s.setItem(k, v);
      },
    };
  } catch {
    const refuse = (): never => {
      throw new Error("storage unavailable");
    };
    return { getItem: refuse, setItem: refuse };
  }
}

const nowIso = (): string => new Date().toISOString();
let storeMap: Map<string, { store: BetStore; actions: BetActions }> | null = null;

function storeFor(scope: WorkspaceScope): { store: BetStore; actions: BetActions } {
  const stores = (storeMap ??= new Map<string, { store: BetStore; actions: BetActions }>());
  const key = scopeId(scope);
  let s = stores.get(key);
  if (!s) {
    const store = createBetStore({ storage: browserStorage(), scope, nowIso });
    s = { store, actions: betActions(store, nowIso) };
    stores.set(key, s);
    void store.load();
  }
  return s;
}

export type BetHook = { ready: false } | { ready: true; scope: WorkspaceScope; store: BetStore; view: BetView; actions: BetActions };

/** The viewer's workspace. While the platform has not said who is looking, `ready` is false (wait, never guess). */
export function useBet(): BetHook {
  const id = useIdentity();
  const scope = scopeForViewer({ source: id.source, id: id.id, loading: id.loading });
  const entry = scope ? storeFor(scope) : null;
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!entry) return;
    const off = entry.store.subscribe(() => {
      setTick((t) => t + 1);
    });
    // Another tab saved: pick it up unless this tab holds an unsaved edit (that stays, and a save then says "conflict").
    const onStorage = (e: StorageEvent): void => {
      if (e.key === storageKey(entry.store.scope) && !entry.store.get().dirty) void entry.store.load();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      off();
      window.removeEventListener("storage", onStorage);
    };
  }, [entry]);
  if (!scope || !entry) return { ready: false };
  return { ready: true, scope, store: entry.store, view: entry.store.get(), actions: entry.actions };
}

/** Says how an action went, in a toast. Returns whether it worked. */
export function say(done: Done, title = "Slip"): boolean {
  if (done.ok) {
    if (done.text) pushToast(title, done.text);
    return true;
  }
  pushToast(`${title}: not done`, done.text, 8000);
  return false;
}

/** The current time, refreshed every `ms` (quote ages and freshness move while a screen is open). */
export function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => {
      setNow(Date.now());
    }, ms);
    return () => {
      window.clearInterval(t);
    };
  }, [ms]);
  return now;
}

// ------------------------------------------------------------------------------------------------ the panel's state

// Nothing here runs at module load (a call there could survive tree-shaking in a build with the switch off).
const PANEL_KEY = "ph.bet.panel";
let panelOpen: boolean | null = null;
let panelListeners: Set<() => void> | null = null;

function panelState(): { open: boolean; listeners: Set<() => void> } {
  panelOpen ??= readKey(PANEL_KEY) === "open";
  panelListeners ??= new Set();
  return { open: panelOpen, listeners: panelListeners };
}

/** Opens or collapses the slip panel (remembered on this device for the desktop panel). */
export function setPanelOpen(open: boolean): void {
  panelOpen = open;
  writeKey(PANEL_KEY, open ? "open" : "closed");
  for (const l of panelState().listeners) l();
}

export function usePanelOpen(): boolean {
  const [open, setOpen] = useState(() => panelState().open);
  useEffect(() => {
    const panelListeners = panelState().listeners;
    const on = (): void => {
      setOpen(panelState().open);
    };
    panelListeners.add(on);
    on();
    return () => {
      panelListeners.delete(on);
    };
  }, []);
  return open;
}

/** True on a wide screen (the panel sits beside the page; below this it is a bottom sheet). */
export function useWide(min = 1024): boolean {
  const q = `(min-width: ${String(min)}px)`;
  const [wide, setWide] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(q).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const m = window.matchMedia(q);
    const on = (): void => {
      setWide(m.matches);
    };
    m.addEventListener("change", on);
    on();
    return () => {
      m.removeEventListener("change", on);
    };
  }, [q]);
  return wide;
}
