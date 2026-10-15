import type {
  CardView,
  GameDetail,
  GameResult,
  Meta,
  ProbAtLine,
  ParlayState,
  Slate,
  SlateListItem,
  SlateResults,
  SwipeDeck,
} from "./types";
import type { LegBreakdown, Side } from "./legTypes";
import type { TopParlays, TpResult } from "./topParlays";

/** Result of a fetch: the data, and whether the service worker served a saved copy. */
export interface Loaded<T> {
  data: T;
  fromCache: boolean;
}

export async function getJson<T>(path: string): Promise<Loaded<T>> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  return { data: (await res.json()) as T, fromCache: res.headers.get("X-From-Cache") === "1" };
}

/** Top-parlays file for a date. 404 means no file (a first-class state); 422 means the API refused it. */
export async function getTopParlays(date: string): Promise<Loaded<TpResult>> {
  const res = await fetch(`/api/top-parlays/${encodeURIComponent(date)}`, { headers: { Accept: "application/json" } });
  const fromCache = res.headers.get("X-From-Cache") === "1";
  if (res.status === 404) return { data: { kind: "missing" }, fromCache };
  if (res.status === 422) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    return { data: { kind: "refused", detail: body.detail ?? "refused" }, fromCache };
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for /api/top-parlays/${date}`);
  return { data: { kind: "ok", data: (await res.json()) as TopParlays }, fromCache };
}

export const api = {
  topParlays: getTopParlays,
  meta: () => getJson<Meta>("/api/meta"),
  slates: () => getJson<SlateListItem[]>("/api/slates"),
  slate: (date: string) => getJson<Slate>(`/api/slates/${encodeURIComponent(date)}`),
  game: (id: string) => getJson<GameDetail>(`/api/games/${encodeURIComponent(id)}`),
  gameResult: (id: string) => getJson<GameResult>(`/api/games/${encodeURIComponent(id)}/result`),
  results: (date: string) => getJson<SlateResults>(`/api/slates/${encodeURIComponent(date)}/results`),
  swipe: (date: string, seed: number, n = 24) =>
    getJson<SwipeDeck>(`/api/slates/${encodeURIComponent(date)}/swipe?seed=${seed}&n=${n}`),
  breakdown: (gameId: string, playerId: number, stat: string, line: number | null, side: Side) =>
    getJson<LegBreakdown>(
      `/api/breakdown/${encodeURIComponent(gameId)}/${playerId.toString()}/${encodeURIComponent(stat)}?side=${side}${
        line === null ? "" : `&line=${line.toString()}`
      }`,
    ),
  card: (gameId: string, playerId: number, stat: string) =>
    getJson<CardView>(`/api/card/${encodeURIComponent(gameId)}/${playerId.toString()}/${encodeURIComponent(stat)}`),
  parlay: () => getJson<ParlayState>("/api/parlay-of-the-day"),
  propAtLine: (gameId: string, playerId: number, stat: string, line: number) =>
    getJson<ProbAtLine>(
      `/api/props/${encodeURIComponent(gameId)}/${playerId.toString()}/${encodeURIComponent(stat)}?line=${line.toString()}`,
    ),
};
