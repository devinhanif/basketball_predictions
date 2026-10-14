import type {
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

export const api = {
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
  parlay: () => getJson<ParlayState>("/api/parlay-of-the-day"),
  propAtLine: (gameId: string, playerId: number, stat: string, line: number) =>
    getJson<ProbAtLine>(
      `/api/props/${encodeURIComponent(gameId)}/${playerId.toString()}/${encodeURIComponent(stat)}?line=${line.toString()}`,
    ),
};
