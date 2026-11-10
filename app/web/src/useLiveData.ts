// Small data hooks for the live screens: the night's live list (polled while the tab is visible) and the app's own
// game (lines and calibrated chances) matched to a live game by night and teams.
import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import { findLive, type LiveGameSummary, type LiveList } from "./live";
import type { GameDetail } from "./types";

export interface LiveListState {
  loading: boolean;
  error: boolean;
  /** One entry per requested date, in order. */
  lists: LiveList[];
  /** performance.now() of the newest successful read (the lists carry their own ages). */
  receivedMs: number;
}

/** The live list for one or more slate dates, re-read every `everyMs` while the tab is visible. */
export function useLiveList(dates: string[], everyMs = 5000): LiveListState {
  const key = dates.join(",");
  const [st, setSt] = useState<LiveListState>({ loading: true, error: false, lists: [], receivedMs: 0 });
  useEffect(() => {
    let live = true;
    const pull = (): void => {
      if (document.hidden) return;
      Promise.all(dates.map((d) => api.liveList(d).then((r) => r.data))).then(
        (lists) => {
          if (live) setSt({ loading: false, error: false, lists, receivedMs: performance.now() });
        },
        () => {
          if (live) setSt((s) => ({ ...s, loading: false, error: true }));
        },
      );
    };
    pull();
    const t = window.setInterval(pull, everyMs);
    const onVis = (): void => {
      if (!document.hidden) pull();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      live = false;
      window.clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [key, everyMs]);
  return st;
}

/** The live game for an app game (same night, same teams), or null. */
export function useLiveFor(g: GameDetail | null, everyMs = 15000): LiveGameSummary | null {
  const st = useLiveList(g ? [g.date] : [], everyMs);
  if (!g || st.lists.length === 0) return null;
  return findLive((st.lists[0] as LiveList).games, g.home.abbr, g.away.abbr);
}

/** The app's game for a live game: its lines and calibrated chances. Never blocks the live feed. */
export function useAppGame(date: string | null, home: string | null, away: string | null): { game: GameDetail | null; loading: boolean } {
  const [st, setSt] = useState<{ game: GameDetail | null; loading: boolean }>({ game: null, loading: date !== null });
  useEffect(() => {
    if (date === null || home === null || away === null) {
      setSt({ game: null, loading: false });
      return;
    }
    let live = true;
    setSt({ game: null, loading: true });
    api
      .slate(date)
      .then((s) => {
        const hit = s.data.games.find((x) => x.home.abbr === home && x.away.abbr === away);
        return hit ? api.game(hit.game_id).then((r) => r.data) : null;
      })
      .then(
        (game) => {
          if (live) setSt({ game, loading: false });
        },
        () => {
          if (live) setSt({ game: null, loading: false });
        },
      );
    return () => {
      live = false;
    };
  }, [date, home, away]);
  return st;
}
