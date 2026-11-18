// Optional files (the desk export, a night's ticket list) are absent most of the time, and a 404 for them is a normal
// answer. Remember that answer for a short while in this tab so every reload does not ask again and log a 404. Only
// "absent" is remembered, never a failure, and only for MISS_TTL_MS: a file written later is seen after that.

export const MISS_TTL_MS = 10 * 60 * 1000;
const PREFIX = "ph.miss.";

interface StoreLike {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
}

function store(): StoreLike | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

/** Was `key` answered "absent" within the last MISS_TTL_MS in this tab? */
export function missedRecently(key: string, now: number = Date.now(), s: StoreLike | null = store()): boolean {
  try {
    const at = Number(s?.getItem(PREFIX + key) ?? "");
    return Number.isFinite(at) && at > 0 && now - at >= 0 && now - at < MISS_TTL_MS;
  } catch {
    return false;
  }
}

/** Remember that `key` was absent just now. */
export function noteMiss(key: string, now: number = Date.now(), s: StoreLike | null = store()): void {
  try {
    s?.setItem(PREFIX + key, now.toString());
  } catch {
    /* storage blocked: we simply ask again next time */
  }
}

/** Forget a remembered miss (the file was found). */
export function clearMiss(key: string, s: StoreLike | null = store()): void {
  try {
    s?.removeItem(PREFIX + key);
  } catch {
    /* nothing to forget */
  }
}
