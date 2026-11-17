// Pure helpers behind the components, so they can be tested without a DOM.
import type { IconName } from "../icons";
import { ageHours, agoText } from "../time";

/** Where Tab or Shift-Tab goes inside a dialog: wraps at both ends; from outside the list it goes to the first (or last). */
export function trapIndex(count: number, current: number, back: boolean): number {
  if (count <= 0) return -1;
  if (current < 0) return back ? count - 1 : 0;
  if (back) return current === 0 ? count - 1 : current - 1;
  return current === count - 1 ? 0 : current + 1;
}

/** The tab a key moves to in a tab list (roving tabindex): arrows wrap, Home and End jump. Null for any other key. */
export function tabKey(key: string, current: number, count: number, vertical = false): number | null {
  if (count <= 0) return null;
  const next = vertical ? "ArrowDown" : "ArrowRight";
  const prev = vertical ? "ArrowUp" : "ArrowLeft";
  if (key === next) return (current + 1) % count;
  if (key === prev) return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

export type SortDir = "ascending" | "descending";
export interface SortState {
  key: string;
  dir: SortDir;
}

/** Clicking a header: a new column starts at its natural direction; the same column flips. */
export function nextSort(prev: SortState | null, key: string, first: SortDir = "descending"): SortState {
  if (prev === null || prev.key !== key) return { key, dir: first };
  return { key, dir: prev.dir === "ascending" ? "descending" : "ascending" };
}

/** Rows ordered by a stored value; missing values (null, undefined, NaN) always go last whatever the direction. Stable. */
export function sortRows<T>(rows: readonly T[], value: (r: T) => number | string | null | undefined, dir: SortDir): T[] {
  const missing = (v: unknown): boolean => v === null || v === undefined || (typeof v === "number" && Number.isNaN(v));
  const sign = dir === "ascending" ? 1 : -1;
  return rows
    .map((r, i) => ({ r, i, v: value(r) }))
    .sort((a, b) => {
      const ma = missing(a.v);
      const mb = missing(b.v);
      if (ma || mb) return ma === mb ? a.i - b.i : ma ? 1 : -1;
      const av = a.v as number | string;
      const bv = b.v as number | string;
      const c = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return c === 0 ? a.i - b.i : c * sign;
    })
    .map((x) => x.r);
}

// ---------------------------------------------------------------- provenance, freshness, states, side panel


/** Where a number came from. Never inferred here: the caller passes the stored source. */
export type Provenance = "live" | "delayed" | "manual" | "sample";
export const PROVENANCES: readonly Provenance[] = ["live", "delayed", "manual", "sample"];

export interface Tag {
  tone: string;
  icon: IconName;
  word: string;
  /** One plain sentence: what the tag means. */
  meaning: string;
}

export const PROV_LOOK: Record<Provenance, Tag> = {
  live: { tone: "better", icon: "live", word: "Live", meaning: "Read from the source during the current run." },
  delayed: { tone: "thin", icon: "clock", word: "Delayed", meaning: "A real stored value, but read some time ago; it may have moved since." },
  manual: { tone: "info", icon: "pen", word: "Manual", meaning: "Typed in by a person, not read from a feed." },
  sample: { tone: "none", icon: "beaker", word: "Sample", meaning: "Example content for showing the layout. Not real data; never use it for a decision." },
};

/** A stored source string to a provenance, or null when it is not one of the four (shown verbatim by the caller). */
export function provenanceOf(v: string | null | undefined): Provenance | null {
  const k = (v ?? "").trim().toLowerCase();
  return (PROVENANCES as readonly string[]).includes(k) ? (k as Provenance) : null;
}

export type Freshness = "fresh" | "old" | "unknown";

/**
 * How old a stored time reads, against a threshold the caller owns (hours). Unknown when the time is missing, unreadable
 * or in the future. The word always carries the age ("4 h ago"), so the state is never told by colour alone.
 */
export function freshness(at: string | null | undefined, oldAfterHours: number, now: Date = new Date()): { state: Freshness; ago: string | null } {
  if (!at) return { state: "unknown", ago: null };
  const h = ageHours(at, now);
  const ago = agoText(at, now);
  if (h === null || ago === null) return { state: "unknown", ago: null };
  return { state: h > oldAfterHours ? "old" : "fresh", ago };
}

export const FRESH_LOOK: Record<Freshness, Tag> = {
  fresh: { tone: "better", icon: "check", word: "Fresh", meaning: "Newer than this screen's age limit." },
  old: { tone: "thin", icon: "clock", word: "Old", meaning: "Older than this screen's age limit; the source may have stopped updating." },
  unknown: { tone: "none", icon: "info", word: "Time not reported", meaning: "The data does not say when this was stored." },
};

/** The state family: one look per state, each with an icon and a word as well as a colour. */
export type StateKind = "loading" | "empty" | "stale" | "partial" | "error" | "reconnecting";
export const STATE_KINDS: readonly StateKind[] = ["loading", "empty", "stale", "partial", "error", "reconnecting"];

export const STATE_LOOK: Record<StateKind, Tag & { role: "status" | "alert" | "note"; title: string }> = {
  loading: { tone: "model", icon: "reset", word: "Loading", meaning: "The data is on its way.", role: "status", title: "Loading" },
  empty: { tone: "none", icon: "inbox", word: "Nothing here", meaning: "The data answered, and there is nothing to show for this.", role: "note", title: "Nothing to show yet" },
  stale: { tone: "thin", icon: "clock", word: "Out of date", meaning: "What is shown is older than it should be.", role: "status", title: "This may be out of date" },
  partial: { tone: "thin", icon: "half", word: "Partly loaded", meaning: "Some of this screen loaded and some did not; what is missing is named.", role: "status", title: "Only part of this loaded" },
  error: { tone: "worse", icon: "warn", word: "Problem", meaning: "Something failed; the screen says what and what to do.", role: "alert", title: "Something went wrong" },
  reconnecting: { tone: "info", icon: "wifi", word: "Reconnecting", meaning: "The connection dropped; the last copy stays on screen until it is back.", role: "status", title: "Reconnecting" },
};

/** Where the side panel sits: a right-hand rail from this width up, a bottom sheet below it. */
export const SIDEPANEL_RAIL_MIN = 1280;

export type PanelLayout = "none" | "rail" | "sheet-closed" | "sheet-open";

/** Nothing to show costs nothing; wide screens get a rail (always open); narrow ones a sheet that opens on request. */
export function panelLayout(width: number, hasContent: boolean, sheetOpen: boolean): PanelLayout {
  if (!hasContent) return "none";
  if (width >= SIDEPANEL_RAIL_MIN) return "rail";
  return sheetOpen ? "sheet-open" : "sheet-closed";
}

/** Only an open sheet is modal: it traps focus and locks the page behind it. */
export function panelIsModal(layout: PanelLayout): boolean {
  return layout === "sheet-open";
}
