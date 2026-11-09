import { useEffect, useState } from "preact/hooks";
import { ScrollNav, restoreScroll } from "./scrollNav";
import { isLegStat, type LegStat, type Side } from "./legTypes";
import { IS_ARTIFACT } from "./env";
import { getPrefs, type Screen } from "./prefs";

export type Route =
  | { name: "today"; date: string | null }
  | { name: "game"; id: string }
  | { name: "leg"; gameId: string; playerId: number; stat: LegStat; side: Side }
  | { name: "swipe"; date: string | null }
  | { name: "picks" }
  | { name: "parlay"; date: string | null }
  | { name: "settings" }
  | { name: "signals" }
  | { name: "hedge"; game: string | null; player: number | null; stat: string | null }
  | { name: "history" }
  | { name: "alerts" }
  | { name: "admin" };

/** The top-level views, in nav order. `today` is the Discover screen; `settings` is Preferences. */
export type TopName = "today" | "signals" | "hedge" | "history" | "alerts" | "swipe" | "picks" | "admin" | "settings";
const ARTIFACT_NAME: Record<TopName, string> = {
  today: "discover", signals: "signals", hedge: "hedge", history: "history", alerts: "alerts", swipe: "swipe", picks: "picks", admin: "admin", settings: "prefs",
};

/** Hash for a leg breakdown. The line is not part of it: the page owns the line while you drag it. */
export function legHref(gameId: string, playerId: number, stat: LegStat, side: Side = "over"): string {
  return `#/leg/${encodeURIComponent(gameId)}/${playerId.toString()}/${stat}${side === "under" ? "?side=under" : ""}`;
}

/** Hash for a game page. */
export function gameHref(gameId: string): string {
  return `#/game/${encodeURIComponent(gameId)}`;
}

/** Hash for a top-level view. The artifact frame passes only plain `#name` hashes, so there they carry no slash. */
export function topHref(name: TopName, artifact: boolean = IS_ARTIFACT): string {
  if (artifact) return `#${ARTIFACT_NAME[name]}`;
  return name === "today" ? "#/" : `#/${name === "settings" ? "prefs" : name}`;
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
    case "settings":
    case "prefs":
      return { name: "settings" };
    case "signals":
      return { name: "signals" };
    case "hedge":
      return { name: "hedge", game: q.get("game"), player: q.get("player") ? Number(q.get("player")) : null, stat: q.get("stat") };
    case "history":
      return { name: "history" };
    case "alerts":
      return { name: "alerts" };
    case "admin":
      return { name: "admin" };
    default:
      return { name: "today", date: q.get("date") };
  }
}

/** Hash for a night's slate. */
export function nightHref(date: string): string {
  return `#/?date=${encodeURIComponent(date)}`;
}

const SCREEN_HASH: Record<Screen, string> = { today: "", signals: "#/signals", swipe: "#/swipe", picks: "#/picks" };

/** The route to open at start: the hash if there is one, else the device's default screen. */
export function startRoute(hash: string, screen: Screen = getPrefs().screen): Route {
  if (hash.replace(/^#\/?/, "") === "") return parse(SCREEN_HASH[screen]);
  return parse(hash);
}

/** Is this the plain hash of a top-level view (what the artifact frame understands)? */
export function plainName(route: Route): TopName | null {
  switch (route.name) {
    case "today":
      return route.date === null ? "today" : null;
    case "swipe":
      return route.date === null ? "swipe" : null;
    case "hedge":
      return route.game === null ? "hedge" : null;
    case "picks":
    case "settings":
    case "signals":
    case "history":
    case "alerts":
    case "admin":
      return route.name;
    default:
      return null;
  }
}

/** The hash the artifact frame may be given for a route: a plain name or nothing (detail pages stay in memory). */
export function hashForRoute(route: Route, artifact: boolean): string | null {
  const plain = plainName(route);
  return plain === null ? null : topHref(plain, artifact);
}

function useArtifactRoute(): Route {
  const [route, setRoute] = useState<Route>(() => startRoute(window.location.hash));
  useEffect(() => {
    const go = (href: string): void => {
      const next = parse(href);
      setRoute(next);
      window.scrollTo(0, 0);
      const want = hashForRoute(next, true);
      if (want !== null && window.location.hash !== want) window.location.hash = want;
    };
    const onClick = (e: MouseEvent): void => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target instanceof Element ? e.target.closest("a") : null;
      const href = a?.getAttribute("href") ?? "";
      if (!href.startsWith("#") || href === "#main" || href === "#") return;
      e.preventDefault();
      go(href);
    };
    const onHash = (): void => {
      setRoute(parse(window.location.hash));
    };
    window.addEventListener("hashchange", onHash);
    document.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("hashchange", onHash);
      document.removeEventListener("click", onClick);
    };
  }, []);
  return route;
}

function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => startRoute(window.location.hash));
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

export function useRoute(): Route {
  // The build is one or the other for its whole life, so the hook order never changes.
  return IS_ARTIFACT ? useArtifactRoute() : useHashRoute();
}
