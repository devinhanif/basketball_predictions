// Per-device preferences: theme, Simple or Analyst, and the screen that opens first. No account; localStorage with a
// try/catch (private windows and sandboxed frames can block it), and an in-memory fallback so a choice still holds for the
// visit.
import { useEffect, useState } from "preact/hooks";

export type Theme = "system" | "light" | "dark";
export type Mode = "simple" | "analyst";
export type Screen = "today" | "signals" | "swipe" | "picks";
/**
 * The market side, hidden or shown (2026-11-24: Learn and Explore folded into one no-prices view). "base" is the default and
 * shows everything except prices, edges and fees (including the what-ifs you can move); "market" adds the existing market views.
 * The stored key stays `ph.lens`; an old stored `learn` or `explore` reads as "base".
 */
export type Lens = "base" | "market";
export const LENSES: readonly Lens[] = ["base", "market"];

/** How a posted American price is written: as posted (the default, what the app always showed) or as decimal odds. */
export type Odds = "american" | "decimal";
/** Comfortable (the default) or compact panel padding; a token switch only. */
export type Density = "comfortable" | "compact";

export interface Prefs {
  theme: Theme;
  mode: Mode;
  screen: Screen;
  lens: Lens;
  odds: Odds;
  density: Density;
}

export const KEYS = { theme: "ph.theme", mode: "ph.mode", screen: "ph.screen", lens: "ph.lens", odds: "ph.odds", density: "ph.density" } as const;
export const DEFAULTS: Prefs = { theme: "system", mode: "simple", screen: "today", lens: "base", odds: "american", density: "comfortable" };

const memory = new Map<string, string>();

export function readKey(key: string): string | null {
  try {
    return localStorage.getItem(key) ?? memory.get(key) ?? null;
  } catch {
    return memory.get(key) ?? null;
  }
}

export function writeKey(key: string, value: string): void {
  memory.set(key, value);
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the choice lasts for this visit only */
  }
}

export function parseTheme(v: string | null): Theme {
  return v === "light" || v === "dark" ? v : "system";
}
export function parseMode(v: string | null): Mode {
  return v === "analyst" ? "analyst" : "simple";
}
export function parseScreen(v: string | null): Screen {
  return v === "signals" || v === "swipe" || v === "picks" ? v : "today";
}

export function parseLens(v: string | null): Lens {
  return v === "market" ? "market" : "base";
}

export function parseOdds(v: string | null): Odds {
  return v === "decimal" ? "decimal" : "american";
}

export function parseDensity(v: string | null): Density {
  return v === "compact" ? "compact" : "comfortable";
}

let current: Prefs | null = null;
const listeners = new Set<() => void>();

export function getPrefs(): Prefs {
  current ??= {
    theme: parseTheme(readKey(KEYS.theme)),
    mode: parseMode(readKey(KEYS.mode)),
    screen: parseScreen(readKey(KEYS.screen)),
    lens: parseLens(readKey(KEYS.lens)),
    odds: parseOdds(readKey(KEYS.odds)),
    density: parseDensity(readKey(KEYS.density)),
  };
  return current;
}

/** Puts the theme on the root element: light and dark are explicit, system leaves it to the media query. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  if (theme === "system") delete document.documentElement.dataset["theme"];
  else document.documentElement.dataset["theme"] = theme;
  applyHeadColor(theme);
}

/** The browser bar's colour per theme: the same two values index.html declares for the system's light and dark. */
export const HEAD_COLOR = { light: "#fbfafd", dark: "#0d0b12" } as const;

/** A theme picked in the app also colours the browser bar; "system" puts back the two media-keyed defaults. */
function applyHeadColor(theme: Theme): void {
  for (const meta of Array.from(document.querySelectorAll('meta[name="theme-color"]'))) {
    const forDark = (meta.getAttribute("media") ?? "").includes("dark");
    meta.setAttribute("content", theme === "system" ? (forDark ? HEAD_COLOR.dark : HEAD_COLOR.light) : HEAD_COLOR[theme]);
  }
}

/** Puts the density on the root element; comfortable is the default and leaves no attribute. */
export function applyDensity(density: Density): void {
  if (typeof document === "undefined") return;
  if (density === "comfortable") delete document.documentElement.dataset["density"];
  else document.documentElement.dataset["density"] = density;
}

/** Puts the detail level on the root element (the same colour map in both; Simple only fills more and is larger). */
export function applyMode(mode: Mode): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset["mode"] = mode;
}

export function setPref<K extends keyof Prefs>(key: K, value: Prefs[K]): void {
  current = { ...getPrefs(), [key]: value };
  writeKey(KEYS[key], value);
  if (key === "theme") applyTheme(value as Theme);
  if (key === "density") applyDensity(value as Density);
  if (key === "mode") applyMode(value as Mode);
  for (const l of listeners) l();
}

/** Test hook: forget what was read. */
export function resetPrefsCache(): void {
  current = null;
  memory.clear();
}

export function usePrefs(): Prefs {
  const [p, setP] = useState(getPrefs);
  useEffect(() => {
    const on = (): void => {
      setP(getPrefs());
    };
    listeners.add(on);
    on();
    return () => {
      listeners.delete(on);
    };
  }, []);
  return p;
}

export function useMode(): Mode {
  return usePrefs().mode;
}

export function useLens(): Lens {
  return usePrefs().lens;
}

/** Is the page showing dark right now? An explicit choice wins; otherwise the system setting. */
/** Landing screens that need the Market lens (their subject is prices and edges). */
export const MARKET_ONLY_SCREENS: readonly Screen[] = ["signals"];

export function screenAllowed(screen: Screen, lens: Lens): boolean {
  return lens === "market" || !MARKET_ONLY_SCREENS.includes(screen);
}

export function effectiveDark(theme: Theme): boolean {
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}
