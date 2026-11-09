// The artifact's data: answers the API gave at bake time (app/api bake_artifact.py), looked up by the path a page asks
// for. Nothing is computed here except the swipe deck's random draw from the baked card pool. Small answers sit in
// plain JSON; the big ones sit in gzip shards (one per game, one per night) that are opened the first time they are
// asked for.
import type { Raw } from "./transport";

export interface BakedEntry {
  s: number;
  b: unknown;
}

export interface Baked {
  format: number;
  built_at: string;
  data_as_of: string;
  export_date: string | null;
  data_version: string | null;
  dates: string[];
  swipe_nights: string[];
  simple: Record<string, BakedEntry>;
  shards: Record<string, string>;
}

export function isBaked(x: unknown): x is Baked {
  if (typeof x !== "object" || x === null) return false;
  const o = x as Record<string, unknown>;
  return (
    o["format"] === 1 &&
    typeof o["data_as_of"] === "string" &&
    Array.isArray(o["dates"]) &&
    Array.isArray(o["swipe_nights"]) &&
    typeof o["simple"] === "object" &&
    o["simple"] !== null &&
    typeof o["shards"] === "object" &&
    o["shards"] !== null
  );
}

/** Which shard holds an API path: one per game (game, result, breakdowns, edges, cards) and one per night. */
export function shardOf(pathname: string): string | null {
  const m = /^\/api\/(games|breakdown|edges|card)\/([^/]+)/.exec(pathname);
  if (m?.[2]) return `g:${decodeURIComponent(m[2])}`;
  const n = /^\/api\/slates\/([^/]+)\/(results|swipe)$/.exec(pathname);
  if (n?.[1]) return `d:${decodeURIComponent(n[1])}`;
  return null;
}

/** mulberry32: a small seeded generator, so the same seed deals the same deck. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function seededShuffle<T>(xs: readonly T[], seed: number): T[] {
  const out = [...xs];
  const r = rng(seed);
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(r() * (i + 1));
    const a = out[i] as T;
    out[i] = out[j] as T;
    out[j] = a;
  }
  return out;
}

export async function gunzipBase64(b64: string): Promise<string> {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).text();
}

const STATUS_TEXT: Record<number, string> = { 200: "OK", 404: "Not Found" };

function raw(status: number, body: unknown): Raw {
  return { status, statusText: STATUS_TEXT[status] ?? "", body, fromCache: false };
}

interface Pool {
  date: string;
  clock_utc: string;
  cards: { tip_utc: string }[];
  note: string;
}

export interface BakedStore {
  get(path: string): Promise<Raw>;
}

export function createBakedStore(baked: Baked, gunzip: (b64: string) => Promise<string> = gunzipBase64): BakedStore {
  const opened = new Map<string, Promise<Record<string, BakedEntry>>>();
  const shard = (name: string): Promise<Record<string, BakedEntry>> => {
    let p = opened.get(name);
    if (!p) {
      const b64 = baked.shards[name];
      p = b64 === undefined ? Promise.resolve({}) : gunzip(b64).then((t) => JSON.parse(t) as Record<string, BakedEntry>);
      opened.set(name, p);
    }
    return p;
  };
  const missing = (): Raw => raw(404, { detail: "that page is not part of this snapshot" });

  async function entry(key: string, pathname: string): Promise<BakedEntry | null> {
    const simple = baked.simple[key];
    if (simple) return simple;
    const name = shardOf(pathname);
    if (name === null) return null;
    return (await shard(name))[key] ?? null;
  }

  return {
    async get(path: string): Promise<Raw> {
      const u = new URL(path, "snap://x");
      const pathname = u.pathname;
      if (pathname === "/api/slates") {
        const e = baked.simple[pathname];
        if (!e || !Array.isArray(e.b)) return missing();
        const rows = (e.b as { date: string }[]).map((s) => ({ ...s, deck: baked.swipe_nights.includes(s.date) }));
        return raw(e.s, rows);
      }
      const sw = /^\/api\/slates\/([^/]+)\/swipe$/.exec(pathname);
      if (sw?.[1]) {
        const date = decodeURIComponent(sw[1]);
        const seed = Math.max(0, Math.floor(Number(u.searchParams.get("seed") ?? "0")) || 0);
        const n = Math.min(60, Math.max(1, Math.floor(Number(u.searchParams.get("n") ?? "24")) || 24));
        const e = await entry(pathname, pathname);
        if (!e) {
          if (!baked.dates.includes(date)) return missing();
          return raw(200, {
            date,
            clock_utc: baked.data_as_of,
            seed,
            cards: [],
            note: "No cards for this night: it has not been played in this snapshot, so there is nothing to grade picks against. Pick from a night that has finished.",
          });
        }
        const pool = e.b as Pool;
        const live = pool.cards.filter((c) => c.tip_utc > pool.clock_utc);
        return raw(200, {
          date,
          clock_utc: pool.clock_utc,
          seed,
          cards: seededShuffle(live, seed).slice(0, n),
          note: pool.note,
        });
      }
      let key = pathname;
      if (pathname.startsWith("/api/breakdown/")) {
        key = `${pathname}?side=${u.searchParams.get("side") === "under" ? "under" : "over"}`;
      }
      const e = await entry(key, pathname);
      return e ? raw(e.s, e.b) : missing();
    },
  };
}
