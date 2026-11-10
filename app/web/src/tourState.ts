// The first-visit tour: whether this device has seen it, and whether it is open now. Storage through prefs.ts, which
// survives blocked storage with an in-memory fallback.
import { useEffect, useState } from "preact/hooks";
import { readKey, writeKey } from "./prefs";

export const TOUR_KEY = "ph.tour";

export function tourSeen(): boolean {
  return readKey(TOUR_KEY) === "1";
}

let open = false;
const listeners = new Set<() => void>();
const tell = (): void => {
  for (const l of listeners) l();
};

export function isTourOpen(): boolean {
  return open;
}
/** Open it, from the profile menu, About, or the first visit. */
export function openTour(): void {
  open = true;
  tell();
}
/** Close it. Finishing and skipping both count as seen, so it does not return on its own. */
export function closeTour(): void {
  open = false;
  writeKey(TOUR_KEY, "1");
  tell();
}
/** Test hook. */
export function resetTour(): void {
  open = false;
  tell();
}

export function useTourOpen(): boolean {
  const [v, setV] = useState(open);
  useEffect(() => {
    const on = (): void => {
      setV(open);
    };
    listeners.add(on);
    on();
    return () => {
      listeners.delete(on);
    };
  }, []);
  return v;
}
