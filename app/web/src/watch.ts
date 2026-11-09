// The watchlist: contracts a viewer is keeping an eye on. Kept on this device; in the shared page also written to the
// viewer's own document watch/<their id> (everyone reads all, each writes only their own, like picks). Ids only: names are
// resolved at render. Other viewers' documents are validated and capped.
import { useEffect, useState } from "preact/hooks";
import { IS_ARTIFACT } from "./env";
import { getPlatform, type Platform } from "./platform";
import { readKey, writeKey } from "./prefs";
import { isUsableId } from "./sharedPicks";

export const MAX_WATCH = 100;
const KEY = "ph.watch.v1";
const DOC_VERSION = 1;

export interface WatchItem {
  /** A stable key for the thing watched, e.g. "<game>:<player>:<stat>:<threshold>:<side>". */
  key: string;
  label: string;
  sub: string;
  at: string;
}

const str = (x: unknown, max: number): string | null => (typeof x === "string" && x.length > 0 && x.length <= max ? x : null);

export function cleanItem(x: unknown): WatchItem | null {
  if (typeof x !== "object" || x === null) return null;
  const o = x as Record<string, unknown>;
  const key = str(o["key"], 90);
  const label = str(o["label"], 80);
  const sub = typeof o["sub"] === "string" && o["sub"].length <= 80 ? o["sub"] : "";
  const at = str(o["at"], 40);
  if (key === null || label === null || at === null || Number.isNaN(Date.parse(at))) return null;
  return { key, label, sub, at };
}

export function cleanWatchDoc(data: unknown): WatchItem[] {
  if (typeof data !== "object" || data === null) return [];
  const o = data as Record<string, unknown>;
  if (o["v"] !== DOC_VERSION || !Array.isArray(o["items"])) return [];
  const seen = new Set<string>();
  const out: WatchItem[] = [];
  for (const raw of (o["items"] as unknown[]).slice(0, MAX_WATCH)) {
    const it = cleanItem(raw);
    if (it && !seen.has(it.key)) {
      seen.add(it.key);
      out.push(it);
    }
  }
  return out;
}

/** Add or remove a key; a copy. Adding beyond the cap drops the oldest. */
export function toggleItem(items: readonly WatchItem[], item: WatchItem): WatchItem[] {
  if (items.some((i) => i.key === item.key)) return items.filter((i) => i.key !== item.key);
  return [...items, item].slice(-MAX_WATCH);
}

/** Union of the device's list and the saved document; the earlier `at` wins for a key both have. */
export function mergeWatch(local: readonly WatchItem[], remote: readonly WatchItem[]): WatchItem[] {
  const by = new Map<string, WatchItem>();
  for (const i of [...remote, ...local]) {
    const have = by.get(i.key);
    if (!have || i.at < have.at) by.set(i.key, i);
  }
  return [...by.values()].sort((a, b) => a.at.localeCompare(b.at)).slice(-MAX_WATCH);
}

export interface WatchState {
  mine: WatchItem[];
  others: Record<string, WatchItem[]>;
  names: Record<string, string>;
  status: "local" | "live";
}

const listeners = new Set<() => void>();
let st: WatchState | null = null;
let push: (() => void) | null = null;

function load(): WatchItem[] {
  try {
    const raw = readKey(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.map(cleanItem).filter((i): i is WatchItem => i !== null).slice(-MAX_WATCH) : [];
  } catch {
    return [];
  }
}
export function getWatch(): WatchState {
  st ??= { mine: load(), others: {}, names: {}, status: "local" };
  return st;
}
function set(patch: Partial<WatchState>, persist = false): void {
  st = { ...getWatch(), ...patch };
  if (persist) writeKey(KEY, JSON.stringify(st.mine));
  for (const l of listeners) l();
}
export function toggleWatch(item: Omit<WatchItem, "at">, now: Date = new Date()): void {
  set({ mine: toggleItem(getWatch().mine, { ...item, at: now.toISOString() }) }, true);
  push?.();
}
export function resetWatch(): void {
  st = null;
  push = null;
  started = false;
}
export function useWatch(): WatchState {
  const [s, setS] = useState(getWatch);
  useEffect(() => {
    const on = (): void => {
      setS(getWatch());
    };
    listeners.add(on);
    return () => {
      listeners.delete(on);
    };
  }, []);
  return s;
}

let started = false;
/** Shared page only. */
export async function startWatchSync(platform?: Platform): Promise<void> {
  if ((!IS_ARTIFACT && !platform) || started) return;
  started = true;
  const p = platform ?? (await getPlatform());
  if (!p.db) return;
  const uid = p.user ? await p.user.id().catch(() => null) : null;
  const self = isUsableId(uid) ? uid : null;
  let merged = false;
  let inflight = false;
  let dirty = false;
  const flush = async (): Promise<void> => {
    if (self === null || inflight || !merged || !p.db) return;
    inflight = true;
    try {
      while (dirty) {
        dirty = false;
        try {
          await p.db.doc(`watch/${self}`).set({ v: DOC_VERSION, items: getWatch().mine });
        } catch {
          /* stays on this device */
        }
      }
    } finally {
      inflight = false;
    }
  };
  push = () => {
    dirty = true;
    window.setTimeout(() => {
      void flush();
    }, 400);
  };
  try {
    p.db.collection("watch").onSnapshot(
      (snap) => {
        const others: Record<string, WatchItem[]> = {};
        let mine: WatchItem[] | null = null;
        for (const d of snap.docs) {
          if (!d.exists || !isUsableId(d.id)) continue;
          const items = cleanWatchDoc(d.data());
          if (d.id === self) mine = items;
          else if (items.length > 0) others[d.id] = items;
        }
        set({ others, status: "live" });
        if (!merged && !snap.metadata?.fromCache && self !== null) {
          merged = true;
          const local = getWatch().mine;
          const m = mine ? mergeWatch(local, mine) : local;
          if (JSON.stringify(m) !== JSON.stringify(local)) set({ mine: m }, true);
          if (JSON.stringify(m) !== JSON.stringify(mine ?? [])) {
            dirty = true;
            void flush();
          }
        }
        const ids = Object.keys(others);
        if (p.user && ids.length > 0) {
          void p.user
            .profiles(ids)
            .then((ps) => {
              const names: Record<string, string> = {};
              for (const id of ids) names[id] = ps[id]?.name || "Someone";
              set({ names });
            })
            .catch(() => undefined);
        }
      },
      () => undefined,
    );
  } catch {
    /* local only */
  }
}
