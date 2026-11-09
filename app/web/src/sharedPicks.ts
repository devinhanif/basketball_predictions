// Shared paper picks for the artifact: each viewer writes one document, picks/<their id>, and reads everyone's. When the
// platform db is missing (or not yet resolved) everything stays in this device's ledger exactly as in the regular build.
//
// Rules this file keeps:
//  * Paper only. A shared pick has no stake, no sizing, no money field.
//  * Store ids only. Names are resolved from user.profiles(ids) at render and never written.
//  * Other viewers' documents are untrusted: every field is validated and every string is length-capped.
//  * One subscription for the whole collection, set up once. One write at a time to the viewer's own document.
import { loadLedger, onLedgerChange, replaceLedger, type Pick } from "./ledger";
import { STATS, type Stat } from "./types";

export const MAX_SHARED = 400;
const MAX_STR = 80;
const ID_RE = /^[A-Za-z0-9_.~:@+-]{1,200}$/;
const DOC_VERSION = 1;

/** A pick as it travels: only what the other person's screen needs. */
export interface SharedPick {
  prop_id: string;
  slate_date: string;
  game_id: string;
  player: string;
  team: string;
  opponent: string;
  stat: Stat;
  line: number;
  over_price: number;
  under_price: number;
  tip_utc: string;
  side: "over" | "under";
  made_at: string;
  saw_model: boolean;
}

export function isUsableId(id: unknown): id is string {
  return typeof id === "string" && ID_RE.test(id);
}

function str(x: unknown, max = MAX_STR): string | null {
  return typeof x === "string" && x.length > 0 && x.length <= max ? x : null;
}
function fin(x: unknown): number | null {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}
function iso(x: unknown): string | null {
  const s = str(x, 40);
  return s !== null && !Number.isNaN(Date.parse(s)) ? s : null;
}

/** Validate one pick from a document nobody here controls. Null when anything is off. */
export function cleanShared(x: unknown): SharedPick | null {
  if (typeof x !== "object" || x === null) return null;
  const o = x as Record<string, unknown>;
  const stat = o["stat"];
  const side = o["side"];
  const prop_id = str(o["prop_id"], 60);
  const slate_date = str(o["slate_date"], 10);
  const game_id = str(o["game_id"], 30);
  const player = str(o["player"]);
  const team = str(o["team"], 6);
  const opponent = str(o["opponent"], 6);
  const line = fin(o["line"]);
  const over_price = fin(o["over_price"]);
  const under_price = fin(o["under_price"]);
  const tip_utc = iso(o["tip_utc"]);
  const made_at = iso(o["made_at"]);
  if (
    prop_id === null || slate_date === null || !/^\d{4}-\d{2}-\d{2}$/.test(slate_date) || game_id === null ||
    player === null || team === null || opponent === null || line === null || line < 0 || line > 200 ||
    over_price === null || under_price === null || Math.abs(over_price) > 100000 || Math.abs(under_price) > 100000 ||
    tip_utc === null || made_at === null
  ) {
    return null;
  }
  if (typeof stat !== "string" || !(STATS as readonly string[]).includes(stat)) return null;
  if (side !== "over" && side !== "under") return null;
  return {
    prop_id, slate_date, game_id, player, team, opponent, stat: stat as Stat, line, over_price, under_price,
    tip_utc, side, made_at, saw_model: o["saw_model"] === true,
  };
}

/** Validate a whole picks document: keeps the good picks (capped, one per prop), drops the rest. */
export function cleanDoc(data: unknown): SharedPick[] {
  if (typeof data !== "object" || data === null) return [];
  const o = data as Record<string, unknown>;
  if (o["v"] !== DOC_VERSION || !Array.isArray(o["picks"])) return [];
  const seen = new Set<string>();
  const out: SharedPick[] = [];
  for (const raw of (o["picks"] as unknown[]).slice(0, MAX_SHARED)) {
    const p = cleanShared(raw);
    if (p && !seen.has(p.prop_id)) {
      seen.add(p.prop_id);
      out.push(p);
    }
  }
  return out;
}

export function toShared(p: Pick): SharedPick | null {
  if (p.side === "skip") return null;
  return {
    prop_id: p.prop_id, slate_date: p.slate_date, game_id: p.game_id, player: p.player, team: p.team,
    opponent: p.opponent, stat: p.stat, line: p.line, over_price: p.over_price, under_price: p.under_price,
    tip_utc: p.tip_utc, side: p.side, made_at: p.made_at, saw_model: p.saw_model,
  };
}

/** The document body for a ledger: newest picks first, capped; skips and local-only fields are left out. */
export function docFromPicks(picks: readonly Pick[]): { v: number; picks: SharedPick[] } {
  const shared = picks.map(toShared).filter((p): p is SharedPick => p !== null);
  shared.sort((a, b) => (a.made_at < b.made_at ? 1 : a.made_at > b.made_at ? -1 : 0));
  return { v: DOC_VERSION, picks: shared.slice(0, MAX_SHARED) };
}

/** A shared pick as a ledger row (to merge picks saved elsewhere into this device, or to grade a friend's). */
export function toPick(s: SharedPick): Pick {
  return {
    id: s.prop_id, prop_id: s.prop_id, slate_date: s.slate_date, game_id: s.game_id, player: s.player, team: s.team,
    opponent: s.opponent, stat: s.stat, line: s.line, over_price: s.over_price, under_price: s.under_price,
    tip_utc: s.tip_utc, side: s.side, made_at: s.made_at, slate_clock: s.made_at, response_ms: 0, source: "swipe",
    saw_model: s.saw_model,
  };
}

/** Union of this device's ledger and the viewer's saved document: one row per prop, the earlier answer wins. */
export function mergeLedgers(local: readonly Pick[], remote: readonly SharedPick[]): Pick[] {
  const byProp = new Map<string, Pick>();
  for (const p of local) byProp.set(p.prop_id, p);
  for (const r of remote) {
    const have = byProp.get(r.prop_id);
    if (!have || r.made_at < have.made_at) byProp.set(r.prop_id, toPick(r));
  }
  return [...byProp.values()].sort((a, b) => (a.made_at < b.made_at ? -1 : a.made_at > b.made_at ? 1 : 0));
}

/* ------------------------------------------------------------------ the capability surface (typed by hand: only what is used) */
interface DocSnap {
  id: string;
  exists: boolean;
  data(): Record<string, unknown> | undefined;
}
interface QuerySnap {
  docs: DocSnap[];
  metadata?: { fromCache: boolean };
}
export interface DbLike {
  collection(path: string): {
    onSnapshot(next: (s: QuerySnap) => void, error?: (e: { code: string; message: string }) => void): () => void;
    doc(id: string): { set(data: Record<string, unknown>): Promise<void> };
  };
}
export interface UserLike {
  id(): Promise<string | null>;
  profiles(ids: readonly string[] | string): Promise<Record<string, { id: string; name: string }>>;
}
export interface ClaudeLike {
  use(name: string): Promise<unknown>;
}

export type ShareStatus = "off" | "loading" | "live" | "timeout" | "error";

export interface SharedState {
  status: ShareStatus;
  /** True once we know the viewer's id and can write their document. */
  canWrite: boolean;
  selfId: string | null;
  /** Other viewers' validated picks, by their id. */
  others: Record<string, SharedPick[]>;
  /** Display names as resolved right now; never stored. */
  names: Record<string, string>;
  /** Last write problem, in words, or null. */
  writeError: string | null;
}

const LOAD_TIMEOUT_MS = 10_000;
const WRITE_DEBOUNCE_MS = 400;

export interface SharedDeps {
  claude: ClaudeLike | undefined;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (h: unknown) => void;
}

const subs = new Set<() => void>();
let state: SharedState = { status: "off", canWrite: false, selfId: null, others: {}, names: {}, writeError: null };
let started = false;

export function getShared(): SharedState {
  return state;
}
export function subscribeShared(fn: () => void): () => void {
  subs.add(fn);
  return () => {
    subs.delete(fn);
  };
}
function set(patch: Partial<SharedState>): void {
  state = { ...state, ...patch };
  for (const s of subs) s();
}

/** Test hook. */
export function resetShared(): void {
  state = { status: "off", canWrite: false, selfId: null, others: {}, names: {}, writeError: null };
  started = false;
}

async function capability<T>(claude: ClaudeLike | undefined, name: string): Promise<T | null> {
  try {
    if (!claude || typeof claude.use !== "function") return null;
    return ((await claude.use(name)) as T | null) ?? null;
  } catch {
    return null;
  }
}

/**
 * Light the sharing up. Call once after the first render. Returns when the db/user answers (or are absent); the
 * subscription then lives for the page's life. With no db the state becomes "off" and nothing else happens.
 */
export async function startShared(deps: SharedDeps): Promise<void> {
  if (started) return;
  started = true;
  if (deps.claude === undefined) {
    set({ status: "off" });
    return;
  }
  set({ status: "loading" });
  let clock: unknown = deps.setTimeout(() => {
    if (state.status === "loading") set({ status: "timeout" });
  }, LOAD_TIMEOUT_MS);
  const [db, user] = await Promise.all([capability<DbLike>(deps.claude, "db"), capability<UserLike>(deps.claude, "user")]);
  if (!db) {
    deps.clearTimeout(clock);
    set({ status: "off" });
    return;
  }
  let selfId: string | null = null;
  if (user) {
    try {
      const id = await user.id();
      selfId = isUsableId(id) ? id : null;
    } catch {
      selfId = null;
    }
  }
  set({ selfId, canWrite: selfId !== null });

  let lastWritten = "";
  let inflight = false;
  let dirty = false;
  let timer: unknown = null;
  // No write happens before the first definitive snapshot: a device that has not yet seen the saved document must not
  // replace it with a shorter ledger.
  let mergedOnce = false;
  const mineKey = (): string => JSON.stringify(docFromPicks(loadLedger()));

  const flush = async (): Promise<void> => {
    if (selfId === null || inflight || !mergedOnce) return;
    inflight = true;
    try {
      while (dirty) {
        dirty = false;
        const body = docFromPicks(loadLedger());
        const key = JSON.stringify(body);
        if (key === lastWritten) continue;
        try {
          await db.collection("picks").doc(selfId).set(body);
          lastWritten = key;
          if (state.writeError !== null) set({ writeError: null });
        } catch (e) {
          const code = typeof e === "object" && e !== null && "code" in e ? String(e.code) : "error";
          set({ writeError: code === "quota_exceeded" ? "The shared store is full, so your newest picks are saved on this device only." : "Your picks could not be shared just now. They are saved on this device." });
        }
      }
    } finally {
      inflight = false;
    }
  };

  onLedgerChange(() => {
    if (selfId === null) return;
    dirty = true;
    if (timer !== null) deps.clearTimeout(timer);
    timer = deps.setTimeout(() => {
      timer = null;
      void flush();
    }, WRITE_DEBOUNCE_MS);
  });

  const onSnap = (snap: QuerySnap): void => {
    deps.clearTimeout(clock);
    clock = null;
    const others: Record<string, SharedPick[]> = {};
    let mineRemote: SharedPick[] | null = null;
    for (const d of snap.docs) {
      if (!d.exists || !isUsableId(d.id)) continue;
      const picks = cleanDoc(d.data());
      if (d.id === selfId) mineRemote = picks;
      else if (picks.length > 0) others[d.id] = picks;
    }
    set({ status: "live", others });
    if (mineRemote !== null && !mergedOnce && !snap.metadata?.fromCache) {
      mergedOnce = true;
      lastWritten = JSON.stringify(docFromPicks(mineRemote.map(toPick)));
      const local = loadLedger();
      const merged = mergeLedgers(local, mineRemote);
      if (JSON.stringify(merged) !== JSON.stringify(local)) replaceLedger(merged);
      else if (mineKey() !== lastWritten) {
        dirty = true;
        void flush();
      }
    } else if (selfId !== null && !mergedOnce && !snap.metadata?.fromCache) {
      // First definitive snapshot and no document of ours yet: publish what is on this device.
      mergedOnce = true;
      if (docFromPicks(loadLedger()).picks.length > 0) {
        dirty = true;
        void flush();
      }
    }
    const ids = Object.keys(others);
    if (user && ids.length > 0) {
      void user
        .profiles(ids)
        .then((ps) => {
          const names: Record<string, string> = {};
          for (const id of ids) names[id] = ps[id]?.name || "Someone";
          set({ names });
        })
        .catch(() => {
          /* names stay "Someone" */
        });
    }
  };

  try {
    db.collection("picks").onSnapshot(onSnap, () => {
      deps.clearTimeout(clock);
      set({ status: "error" });
    });
  } catch {
    deps.clearTimeout(clock);
    set({ status: "error" });
  }
}

/** Who is on this prop right now: this device's own pick first, then friends'. Names are display-only. */
export function onIt(propId: string, own: readonly Pick[], s: SharedState): { who: string; side: "over" | "under"; line: number }[] {
  const out: { who: string; side: "over" | "under"; line: number }[] = [];
  const mine = own.find((p) => p.prop_id === propId && p.side !== "skip");
  if (mine && (mine.side === "over" || mine.side === "under")) out.push({ who: "you", side: mine.side, line: mine.line });
  for (const [id, picks] of Object.entries(s.others)) {
    const p = picks.find((x) => x.prop_id === propId);
    if (p) out.push({ who: s.names[id] || "Someone", side: p.side, line: p.line });
  }
  return out;
}
