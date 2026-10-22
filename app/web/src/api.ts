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
import type { TopParlays, TpResult } from "./topParlays";
import type { LegModel } from "./tracker";
import type { History } from "./optionsChart";
import type { ScanResult } from "./importLink";
import type { KalshiPrices, LiveGame, Scoreboard } from "./liveTypes";

/** Result of a fetch: the data, and whether the service worker served a saved copy. */
export interface Loaded<T> {
  data: T;
  fromCache: boolean;
}

export async function getJson<T>(path: string): Promise<Loaded<T>> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(httpErrorMessage(res.status, res.statusText, path, typeof body.detail === "string" ? body.detail : null));
  }
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

/** Send a screenshot to the local scan endpoint. The body is the raw image; nothing is stored by us. */
export async function scanScreenshot(file: File): Promise<ScanResult> {
  const res = await fetch("/api/scan/kalshi-screenshot", { method: "POST", headers: { "Content-Type": file.type }, body: file });
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown } & Partial<ScanResult>;
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `${res.status.toString()} ${res.statusText}`);
  return body as ScanResult;
}

export const api = {
  liveScoreboard: () => getJson<Scoreboard>("/api/live/scoreboard"),
  liveGame: (id: string) => getJson<LiveGame>(`/api/live/game/${encodeURIComponent(id)}`),
  liveModel: (q: URLSearchParams) => getJson<LegModel>(`/api/live/model?${q.toString()}`),
  liveHistory: (tickers: readonly string[]) =>
    getJson<History>(`/api/live/history?tickers=${encodeURIComponent(tickers.join(","))}`),
  kalshi: (tickers: readonly string[]) =>
    getJson<KalshiPrices>(`/api/live/kalshi?tickers=${encodeURIComponent(tickers.join(","))}`),
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

/**
 * After a slate loads, fetch its game pages once so the service worker keeps a copy: a game page opened for the first
 * time while offline still works (F15). Only when the worker is in control; one at a time, errors ignored.
 */
export async function warmGames(ids: readonly string[], limit = 20): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker.controller) return;
  for (const id of ids.slice(0, limit)) {
    try {
      await fetch(`/api/games/${encodeURIComponent(id)}`, { headers: { Accept: "application/json" } });
    } catch {
      return;
    }
  }
}
