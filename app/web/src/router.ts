import { useEffect, useState } from "preact/hooks";
import { ScrollNav, restoreScroll } from "./scrollNav";
import { isLegStat, type LegStat, type Side } from "./legTypes";

export type Route =
  | { name: "today"; date: string | null }
  | { name: "game"; id: string }
  | { name: "leg"; gameId: string; playerId: number; stat: LegStat; side: Side }
  | { name: "swipe"; date: string | null }
  | { name: "picks" }
  | { name: "parlay"; date: string | null }
  | { name: "studio" }
  | { name: "court"; gameId: string | null }
  | { name: "builds"; playerId: number | null }
  | { name: "learned" }
  | { name: "live"; eventId: string | null }
  | { name: "tracker" };

/** Hash for a leg breakdown. The line is not part of it: the page owns the line while you drag it. */
export function legHref(gameId: string, playerId: number, stat: LegStat, side: Side = "over"): string {
  return `#/leg/${encodeURIComponent(gameId)}/${playerId.toString()}/${stat}${side === "under" ? "?side=under" : ""}`;
}

/** Hash for a game page. */
export function gameHref(gameId: string): string {
  return `#/game/${encodeURIComponent(gameId)}`;
}

export function parse(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const q = new URLSearchParams(query);
  switch (parts[0]) {
    case "game":
      return parts[1] ? { name: "game", id: decodeURIComponent(parts[1]) } : { name: "today", date: null };
    case "leg": {
      const [, gameId, pid, stat] = parts;
      const playerId = Number(pid);
      if (gameId && Number.isInteger(playerId) && stat && isLegStat(stat)) {
        return { name: "leg", gameId: decodeURIComponent(gameId), playerId, stat, side: q.get("side") === "under" ? "under" : "over" };
      }
      return { name: "today", date: null };
    }
    case "swipe":
      return { name: "swipe", date: q.get("date") };
    case "picks":
      return { name: "picks" };
    case "parlay":
      return { name: "parlay", date: q.get("date") };
    case "studio":
      return { name: "studio" };
    case "court":
      return { name: "court", gameId: parts[1] ? decodeURIComponent(parts[1]) : null };
    case "builds": {
      const id = Number(parts[1]);
      return { name: "builds", playerId: Number.isInteger(id) && parts[1] ? id : null };
    }
    case "learned":
      return { name: "learned" };
    case "live":
      return { name: "live", eventId: parts[1] ? decodeURIComponent(parts[1]) : null };
    case "tracker":
      return { name: "tracker" };
    default:
      return { name: "today", date: q.get("date") };
  }
}

/** Hash for a night's slate. */
export function nightHref(date: string): string {
  return `#/?date=${encodeURIComponent(date)}`;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    window.history.scrollRestoration = "manual";
    const nav = new ScrollNav(window.location.hash);
    let cancel: (() => void) | null = null;
    let frame = 0;
    const onScroll = (): void => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        nav.onScroll(window.scrollY);
      });
    };
    const onClick = (e: MouseEvent): void => {
      const a = e.target instanceof Element ? e.target.closest("a") : null;
      if (a && (a.getAttribute("href") ?? "").startsWith("#/")) nav.linkClicked();
    };
    const on = (): void => {
      cancel?.();
      cancel = null;
      setRoute(parse(window.location.hash));
      const action = nav.onHashChange(window.location.hash);
      if (action.kind === "top") window.scrollTo(0, 0);
      else cancel = restoreScroll(action.y, () => { nav.restoreDone(); });
    };
    window.addEventListener("hashchange", on);
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("hashchange", on);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("click", onClick, true);
      cancel?.();
    };
  }, []);
  return route;
}
