// Per-device preferences: theme, Simple or Analyst, and the screen that opens first. No account; localStorage with a
// try/catch (private windows and sandboxed frames can block it), and an in-memory fallback so a choice still holds for the
// visit.
import { useEffect, useState } from "preact/hooks";

export type Theme = "system" | "light" | "dark";
export type Mode = "simple" | "analyst";
export type Screen = "today" | "swipe" | "picks";

export interface Prefs {
  theme: Theme;
  mode: Mode;
  screen: Screen;
}

export const KEYS = { theme: "ph.theme", mode: "ph.mode", screen: "ph.screen" } as const;
export const DEFAULTS: Prefs = { theme: "system", mode: "simple", screen: "today" };

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
  return v === "swipe" || v === "picks" ? v : "today";
}

let current: Prefs | null = null;
const listeners = new Set<() => void>();

export function getPrefs(): Prefs {
  current ??= {
    theme: parseTheme(readKey(KEYS.theme)),
    mode: parseMode(readKey(KEYS.mode)),
    screen: parseScreen(readKey(KEYS.screen)),
  };
  return current;
}

/** Puts the theme on the root element: light and dark are explicit, system leaves it to the media query. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  if (theme === "system") delete document.documentElement.dataset["theme"];
  else document.documentElement.dataset["theme"] = theme;
}

export function setPref<K extends keyof Prefs>(key: K, value: Prefs[K]): void {
  current = { ...getPrefs(), [key]: value };
  writeKey(KEYS[key], value);
  if (key === "theme") applyTheme(value as Theme);
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

/** Is the page showing dark right now? An explicit choice wins; otherwise the system setting. */
export function effectiveDark(theme: Theme): boolean {
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}
