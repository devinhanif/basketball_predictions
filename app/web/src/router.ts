import { useEffect, useState } from "preact/hooks";
import { ScrollNav, restoreScroll } from "./scrollNav";
import { isLegStat, type LegStat, type Side } from "./legTypes";
import { IS_ARTIFACT } from "./env";
import { isIsoDate } from "./time";
import { STATS } from "./types";
import { getPrefs, type Screen } from "./prefs";

export type RecordTab = "replay" | "calibration" | "guess";
export const RECORD_TABS: readonly RecordTab[] = ["replay", "calibration", "guess"];

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
  | { name: "record"; tab: RecordTab; date: string | null }
  | { name: "live"; id: string | null; date: string | null }
  | { name: "alerts" }
  | { name: "admin" }
  /** Where a shown forecast came from: a game's win chance (`playerId` null) or one prop. */
  | { name: "source"; gameId: string; playerId: number | null; stat: LegStat | null }
  /** The honest scorecard and the forward-test tracker. */
  | { name: "scorecard" }
  | { name: "tests" }
  /** Error-code help: every code, or one (`code`), with a search box (`q`). */
  | { name: "help"; code: string | null; q: string | null }
  /** Key terms; `term` is the anchor to scroll to, `q` the search box. */
  | { name: "terms"; term: string | null; q: string | null }
  /** Anything the app could not have linked to; `hash` is what was asked for. */
  | { name: "notfound"; hash: string };

/** The top-level views, in nav order. `today` is the Discover screen; `settings` is Preferences. */
export type TopName = "today" | "signals" | "hedge" | "history" | "record" | "live" | "alerts" | "swipe" | "picks" | "admin" | "settings" | "terms" | "scorecard" | "tests" | "help";
const ARTIFACT_NAME: Record<TopName, string> = {
  today: "discover", signals: "signals", hedge: "hedge", history: "history", record: "record", live: "live", alerts: "alerts", swipe: "swipe", picks: "picks", admin: "admin", settings: "prefs", terms: "terms",
  scorecard: "scorecard", tests: "tests", help: "help",
};

/** Hash for a leg breakdown. The line is not part of it: the page owns the line while you drag it. */
export function legHref(gameId: string, playerId: number, stat: LegStat, side: Side = "over"): string {
  return `#/leg/${encodeURIComponent(gameId)}/${playerId.toString()}/${stat}${side === "under" ? "?side=under" : ""}`;
}

/** Hash for a game page. */
export function gameHref(gameId: string): string {
  return `#/game/${encodeURIComponent(gameId)}`;
}

/** Hash for the live screen, or for one live game by its ESPN event id. */
export function liveHref(id: string | null = null, date: string | null = null): string {
  const q = date === null ? "" : `?date=${encodeURIComponent(date)}`;
  return id === null ? `#/live${q}` : `#/live/${encodeURIComponent(id)}${q}`;
}

/** Hash for a top-level view. The artifact frame passes only plain `#name` hashes, so there they carry no slash. */
export function topHref(name: TopName, artifact: boolean = IS_ARTIFACT): string {
  if (artifact) return `#${ARTIFACT_NAME[name]}`;
  if (name === "help") return "#/help/errors";
  return name === "today" ? "#/" : `#/${name === "settings" ? "prefs" : name}`;
}

/** Hash for where a forecast came from: the game's win chance, or a prop when player and stat are given. */
export function sourceHref(gameId: string, playerId: number | null = null, stat: LegStat | null = null): string {
  const g = `#/source/${encodeURIComponent(gameId)}`;
  return playerId === null || stat === null ? g : `${g}/${playerId.toString()}/${stat}`;
}

/** Hash for error-code help, one code or all. */
export function helpHref(code: string | null = null, q: string | null = null): string {
  const base = code === null ? "#/help/errors" : `#/help/errors/${encodeURIComponent(code)}`;
  return q === null || q === "" ? base : `${base}?q=${encodeURIComponent(q)}`;
}

/** A decoded path segment, or null when its percent-encoding is broken (a hand-edited or truncated link). */
function decode(s: string): string | null {
  try {
    return decodeURIComponent(s);
  } catch {
    return null;
  }
}

/** An id the app put in a link: any text, but not empty, not huge and without control characters. */
function okId(s: string): boolean {
  if (s.length === 0 || s.length > 64) return false;
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) < 0x20) return false;
  return true;
}

/** A path segment as an id, or null. */
function id(raw: string | undefined): string | null {
  const s = raw === undefined ? null : decode(raw);
  return s !== null && okId(s) ? s : null;
}

/** A positive whole player id written in plain digits ("1e3", "0x10" and " 12" are not ids). */
function playerId(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined || !/^\d{1,10}$/.test(raw)) return null;
  const n = Number(raw);
  return n > 0 ? n : null;
}


/**
 * The route for a hash. Anything the app could not have linked to (an unknown path, a broken id, an impossible date)
 * is the explicit not-found route, which keeps the hash so the screen can say what was asked for. Never throws.
 */
export function parse(hash: string): Route {
  const notFound: Route = { name: "notfound", hash };
  const raw = hash.replace(/^#/, "");
  const cut = raw.indexOf("?");
  const path = cut < 0 ? raw : raw.slice(0, cut);
  const query = cut < 0 ? "" : raw.slice(cut + 1);
  const parts = path.split("/").filter(Boolean);
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(query);
  } catch {
    return notFound;
  }
  /** The `date` parameter: absent is null; present must be a real date, else the whole route is not found. */
  const date = (): string | null | undefined => {
    const d = q.get("date");
    if (d === null) return null;
    return isIsoDate(d) ? d : undefined;
  };
  const extra = parts.length > 1;
  switch (parts[0]) {
    case undefined:
    case "discover":
    case "today":
    case "main": {
      const d = date();
      return d === undefined || extra ? notFound : { name: "today", date: d };
    }
    case "game": {
      const gid = id(parts[1]);
      return gid !== null && parts.length === 2 ? { name: "game", id: gid } : notFound;
    }
    case "leg": {
      const gid = id(parts[1]);
      const pid = playerId(parts[2]);
      const stat = parts[3];
      if (gid !== null && pid !== null && stat !== undefined && isLegStat(stat) && parts.length === 4) {
        return { name: "leg", gameId: gid, playerId: pid, stat, side: q.get("side") === "under" ? "under" : "over" };
      }
      return notFound;
    }
    case "swipe": {
      const d = date();
      return d === undefined || extra ? notFound : { name: "swipe", date: d };
    }
    case "parlay": {
      const d = date();
      return d === undefined || extra ? notFound : { name: "parlay", date: d };
    }
    case "picks":
    case "settings":
    case "prefs":
    case "signals":
    case "history":
    case "alerts":
    case "admin":
      if (extra) return notFound;
      return parts[0] === "prefs" || parts[0] === "settings" ? { name: "settings" } : { name: parts[0] };
    case "terms": {
      if (extra) return notFound;
      const t = q.get("t");
      const s = q.get("q");
      // A term anchor is a short slug; search text is any short text.
      if ((t !== null && !/^[a-z0-9-]{1,40}$/.test(t)) || (s !== null && s !== "" && !okId(s))) return notFound;
      return { name: "terms", term: t, q: s === "" ? null : s };
    }
    case "source": {
      const gid = id(parts[1]);
      if (gid === null) return notFound;
      if (parts.length === 2) return { name: "source", gameId: gid, playerId: null, stat: null };
      const pid = playerId(parts[2]);
      const stat = parts[3];
      if (pid !== null && stat !== undefined && isLegStat(stat) && parts.length === 4) return { name: "source", gameId: gid, playerId: pid, stat };
      return notFound;
    }
    case "scorecard":
    case "tests":
      return extra || query !== "" ? notFound : { name: parts[0] };
    case "help": {
      // #/help and #/help/errors list every code; #/help/errors/<code> opens one.
      if (parts.length > 3 || (parts[1] !== undefined && parts[1] !== "errors")) return notFound;
      const c = parts[2] === undefined ? null : decode(parts[2]);
      const s = q.get("q");
      if ((c !== null && !/^[a-z_]{1,40}$/.test(c)) || (s !== null && s !== "" && !okId(s))) return notFound;
      return { name: "help", code: c, q: s === null || s === "" ? null : s };
    }
    case "hedge": {
      if (extra) return notFound;
      const g = q.get("game");
      const p = q.get("player");
      const s = q.get("stat");
      const game = g !== null && okId(g) ? g : null;
      const player = p === null ? null : playerId(p);
      if ((g !== null && game === null) || (p !== null && player === null) || (s !== null && !(STATS as readonly string[]).includes(s))) return notFound;
      return { name: "hedge", game, player, stat: s };
    }
    case "record": {
      const t = q.get("tab");
      const d = date();
      if (extra || d === undefined) return notFound;
      // An unknown tab is the replay (an old link), not a missing page.
      const tab: RecordTab = t === "calibration" || t === "guess" ? t : "replay";
      return { name: "record", tab, date: d };
    }
    case "live": {
      const d = date();
      const lid = parts[1] === undefined ? null : id(parts[1]);
      if (d === undefined || parts.length > 2 || (parts[1] !== undefined && lid === null)) return notFound;
      return { name: "live", id: lid, date: d };
    }
    default:
      return notFound;
  }
}

/** Hash for a track-record tab, optionally a night of the replay. */
export function recordHref(tab: RecordTab, date: string | null): string {
  const q = [tab === "replay" ? "" : `tab=${tab}`, date === null ? "" : `date=${encodeURIComponent(date)}`].filter(Boolean).join("&");
  return q === "" ? "#/record" : `#/record?${q}`;
}

/** Hash for Key terms, with an anchor and a search when given. */
export function termsHashFor(term: string | null, q: string | null): string {
  const parts = [term === null ? "" : `t=${encodeURIComponent(term)}`, q === null || q === "" ? "" : `q=${encodeURIComponent(q)}`].filter(Boolean).join("&");
  return parts === "" ? "#/terms" : `#/terms?${parts}`;
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
    case "record":
      return route.tab === "replay" && route.date === null ? "record" : null;
    case "live":
      return route.id === null && route.date === null ? "live" : null;
    case "terms":
      return route.term === null && route.q === null ? "terms" : null;
    case "help":
      return route.code === null && route.q === null ? "help" : null;
    case "scorecard":
    case "tests":
      return route.name;
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
