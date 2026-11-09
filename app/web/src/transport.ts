// The regular build's transport: the browser asks the read-only API on its own origin. The artifact build swaps this
// file for transport.static.ts (a vite alias), so none of this ships in the single-file page.

/** One answer: the status, the parsed body (null when it was not JSON), and whether a service worker served a saved copy. */
export interface Raw {
  status: number;
  statusText: string;
  body: unknown;
  fromCache: boolean;
}

export async function getRaw(path: string): Promise<Raw> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body: unknown = await res.json().catch(() => null);
  return { status: res.status, statusText: res.statusText, body, fromCache: res.headers.get("X-From-Cache") === "1" };
}

/**
 * After a slate loads, fetch its game pages once so the service worker keeps a copy: a game page opened for the first
 * time while offline still works (F15). Only when the worker is in control; one at a time, errors ignored.
 */
export async function warmGames(ids: readonly string[], limit = 20): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker.controller) return;
  for (const id of ids.slice(0, limit)) {
    try {
      await fetch(`/api/games/${encodeURIComponent(id)}`, { headers: { Accept: "application/json" } });
    } catch {
      return;
    }
  }
}
