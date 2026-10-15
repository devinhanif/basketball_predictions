import type { SlateListItem } from "./types";

/** Today's calendar date in US Eastern time, "YYYY-MM-DD" (NBA nights are dated in ET). */
export function todayET(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** Nights oldest to newest, whatever order the API sent them in (F17). */
export function chronological<T extends { date: string }>(slates: readonly T[]): T[] {
  return [...slates].sort((a, b) => a.date.localeCompare(b.date));
}

/**
 * Which night opens by default: today's ET date if the data holds it, else the next upcoming night,
 * else (every night is in the past) the most recent one. Null when there are none.
 */
export function defaultNight(slates: readonly Pick<SlateListItem, "date">[], now: Date = new Date()): string | null {
  const dates = chronological(slates).map((s) => s.date);
  if (dates.length === 0) return null;
  const today = todayET(now);
  return dates.find((d) => d >= today) ?? dates[dates.length - 1] ?? null;
}

/**
 * Does the parlay engine's verdict (stored for `reportDate`) speak for the night on screen? (F05)
 * With no night chosen it does; with a different night it must not be shown as that night's verdict.
 */
export function engineSpeaksFor(date: string | null, reportDate: string): boolean {
  return date === null || date === reportDate;
}
