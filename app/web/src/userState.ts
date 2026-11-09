// Per-viewer state that is private to them: which notices are read, and the notification switches. Always kept on this device
// (localStorage, in-memory fallback). In the shared page it is also mirrored to the viewer's own private document
// data/users/<id>/state (nobody else can read it) so it follows them between devices. The mirror merges, never replaces:
// read ids are a union.
import { useEffect, useState } from "preact/hooks";
import { markRead, NOTIFY_DEFAULTS, type NotifyPrefs } from "./alerts";
import { IS_ARTIFACT } from "./env";
import { getPlatform, type Platform } from "./platform";
import { isUsableId } from "./sharedPicks";
import { readKey, writeKey } from "./prefs";

const READ_KEY = "ph.read.v1";
const NOTIFY_KEY = "ph.notify.v1";
const DOC_VERSION = 1;

export interface UserState {
  read: Set<string>;
  notify: NotifyPrefs;
}

const listeners = new Set<() => void>();
let state: UserState | null = null;
let pushTimer: number | null = null;
let push: (() => void) | null = null;

function parseIds(x: unknown): string[] {
  return Array.isArray(x) ? x.filter((v): v is string => typeof v === "string" && v.length > 0 && v.length <= 80).slice(-500) : [];
}
function parseNotify(x: unknown): NotifyPrefs {
  const o = typeof x === "object" && x !== null ? (x as Record<string, unknown>) : {};
  return {
    fouls: typeof o["fouls"] === "boolean" ? o["fouls"] : NOTIFY_DEFAULTS.fouls,
    risk: typeof o["risk"] === "boolean" ? o["risk"] : NOTIFY_DEFAULTS.risk,
    corrections: typeof o["corrections"] === "boolean" ? o["corrections"] : NOTIFY_DEFAULTS.corrections,
  };
}
function parse(raw: string | null): unknown {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function getUserState(): UserState {
  state ??= { read: new Set(parseIds(parse(readKey(READ_KEY)))), notify: parseNotify(parse(readKey(NOTIFY_KEY))) };
  return state;
}

function commit(next: UserState, sync = true): void {
  state = next;
  writeKey(READ_KEY, JSON.stringify([...next.read]));
  writeKey(NOTIFY_KEY, JSON.stringify(next.notify));
  for (const l of listeners) l();
  if (sync) push?.();
}

export function markIdsRead(ids: readonly string[]): void {
  const s = getUserState();
  commit({ ...s, read: markRead(s.read, ids) });
}
export function setNotify(patch: Partial<NotifyPrefs>): void {
  const s = getUserState();
  commit({ ...s, notify: { ...s.notify, ...patch } });
}
/** Test hook. */
export function resetUserState(): void {
  state = null;
  push = null;
  started = false;
}

export function useUserState(): UserState {
  const [s, setS] = useState(getUserState);
  useEffect(() => {
    const on = (): void => {
      setS(getUserState());
    };
    listeners.add(on);
    return () => {
      listeners.delete(on);
    };
  }, []);
  return s;
}

let started = false;
/** Shared page only: mirror to the viewer's private document, once. */
export async function startUserStateSync(platform?: Platform): Promise<void> {
  if ((!IS_ARTIFACT && !platform) || started) return;
  started = true;
  const p = platform ?? (await getPlatform());
  if (!p.db || !p.user) return;
  const uid = await p.user.id().catch(() => null);
  if (!isUsableId(uid)) return;
  const ref = p.db.doc(`data/users/${uid}/state`);
  let inflight = false;
  let dirty = false;
  let first = true;
  const flush = async (): Promise<void> => {
    if (inflight || first) return;
    inflight = true;
    try {
      while (dirty) {
        dirty = false;
        const s = getUserState();
        try {
          await ref.set({ v: DOC_VERSION, read: [...s.read].slice(-500), notify: s.notify });
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
    if (pushTimer !== null) window.clearTimeout(pushTimer);
    pushTimer = window.setTimeout(() => {
      pushTimer = null;
      void flush();
    }, 500);
  };
  try {
    ref.onSnapshot(
      (snap) => {
        if (snap.metadata?.fromCache) return;
        const d = snap.exists ? snap.data() : undefined;
        const cur = getUserState();
        if (d && d["v"] === DOC_VERSION) {
          const merged = markRead(cur.read, parseIds(d["read"]));
          const changed = merged.size !== cur.read.size;
          if (first) commit({ read: merged, notify: parseNotify(d["notify"]) }, false);
          else if (changed) commit({ ...cur, read: merged }, false);
        }
        if (first) {
          first = false;
          dirty = true;
          void flush();
        }
      },
      () => undefined,
    );
  } catch {
    /* no private document: the state stays on this device */
  }
}
