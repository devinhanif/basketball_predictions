// The artifact build's transport (swapped in for transport.ts by a vite alias): nothing leaves the page. Answers come from
// the data the build inlined into this file.
import { createBakedStore, isBaked, type BakedStore } from "./bakedStore";
import type { Raw } from "./transport";

export type { Raw } from "./transport";

let store: BakedStore | null = null;

function open(): BakedStore | null {
  if (store) return store;
  const el = document.getElementById("ph-baked");
  if (!el?.textContent) return null;
  try {
    const parsed: unknown = JSON.parse(el.textContent);
    if (!isBaked(parsed)) return null;
    store = createBakedStore(parsed);
    return store;
  } catch {
    return null;
  }
}

export async function getRaw(path: string): Promise<Raw> {
  const s = open();
  if (!s) return { status: 503, statusText: "Unavailable", body: { detail: "the data baked into this page could not be read" }, fromCache: false };
  return s.get(path);
}

/** No service worker here, so nothing to warm. */
export function warmGames(): Promise<void> {
  return Promise.resolve();
}
