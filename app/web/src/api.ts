import type {
  Evidence,
  GameDetail,
  GameResult,
  Meta,
  ParlayState,
  Slate,
  SlateListItem,
  SlateResults,
  SwipeDeck,
} from "./types";

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
  parlay: () => getJson<ParlayState>("/api/parlay-of-the-day"),
  evidence: () => getJson<Evidence>("/api/evidence"),
};
