// Pure helpers behind the components, so they can be tested without a DOM.

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
