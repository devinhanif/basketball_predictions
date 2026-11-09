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
import { httpErrorMessage } from "./errors";
import type { LegBreakdown, Side } from "./legTypes";
import type { LegEdges } from "./edges";
import type { TopParlays, TpResult } from "./topParlays";
import { getRaw, warmGames } from "./transport";

export { warmGames };

/** Result of a fetch: the data, and whether the service worker served a saved copy. */
export interface Loaded<T> {
  data: T;
  fromCache: boolean;
}

export async function getJson<T>(path: string): Promise<Loaded<T>> {
  const r = await getRaw(path);
  if (r.status < 200 || r.status >= 300) {
    const detail = (r.body as { detail?: unknown } | null)?.detail;
    throw new Error(httpErrorMessage(r.status, r.statusText, path, typeof detail === "string" ? detail : null));
  }
  return { data: r.body as T, fromCache: r.fromCache };
}

/** Top-parlays file for a date. 404 means no file (a first-class state); 422 means the API refused it. */
export async function getTopParlays(date: string): Promise<Loaded<TpResult>> {
  const path = `/api/top-parlays/${encodeURIComponent(date)}`;
  const r = await getRaw(path);
  if (r.status === 404) return { data: { kind: "missing" }, fromCache: r.fromCache };
  if (r.status === 422) {
    const detail = (r.body as { detail?: string } | null)?.detail;
    return { data: { kind: "refused", detail: detail ?? "refused" }, fromCache: r.fromCache };
  }
  if (r.status < 200 || r.status >= 300) throw new Error(`${r.status.toString()} ${r.statusText} for ${path}`);
  return { data: { kind: "ok", data: r.body as TopParlays }, fromCache: r.fromCache };
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
  edges: (gameId: string, playerId: number, stat: string) =>
    getJson<LegEdges>(`/api/edges/${encodeURIComponent(gameId)}/${playerId.toString()}/${encodeURIComponent(stat)}`),
  propAtLine: (gameId: string, playerId: number, stat: string, line: number) =>
    getJson<ProbAtLine>(
      `/api/props/${encodeURIComponent(gameId)}/${playerId.toString()}/${encodeURIComponent(stat)}?line=${line.toString()}`,
    ),
};
