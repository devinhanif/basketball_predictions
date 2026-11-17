import type { RefObject } from "preact";
import { useEffect, useRef } from "preact/hooks";

/**
 * A row of tabs that scrolls sideways: bring the current one (aria-current="true") into view, moving the row only,
 * never the page. Pass what changes the current tab so a new choice is followed.
 */
export function useCurrentTab<T extends HTMLElement = HTMLElement>(key: string): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const nav = ref.current;
    const on = nav?.querySelector<HTMLElement>('[aria-current="true"]');
    if (nav && on) nav.scrollLeft = Math.max(0, on.offsetLeft - nav.offsetLeft - nav.clientWidth + on.offsetWidth + 8);
  }, [key]);
  return ref;
}
