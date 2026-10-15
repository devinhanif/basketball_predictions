import { useEffect, useState } from "preact/hooks";
import { isLegStat, type LegStat, type Side } from "./legTypes";

export type Route =
  | { name: "today"; date: string | null }
  | { name: "game"; id: string }
  | { name: "leg"; gameId: string; playerId: number; stat: LegStat; side: Side }
  | { name: "swipe"; date: string | null }
  | { name: "picks" }
  | { name: "parlay"; date: string | null };

/** Hash for a leg breakdown. The line is not part of it: the page owns the line while you drag it. */
export function legHref(gameId: string, playerId: number, stat: LegStat, side: Side = "over"): string {
  return `#/leg/${encodeURIComponent(gameId)}/${playerId.toString()}/${stat}${side === "under" ? "?side=under" : ""}`;
}

export function parse(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const q = new URLSearchParams(query);
  switch (parts[0]) {
    case "game":
      return parts[1] ? { name: "game", id: parts[1] } : { name: "today", date: null };
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
    default:
      return { name: "today", date: q.get("date") };
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const on = (): void => {
      setRoute(parse(window.location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => {
      window.removeEventListener("hashchange", on);
    };
  }, []);
  return route;
}
