import { useCallback, useEffect, useRef, useState } from "preact/hooks";

export const POLL_MS = 30_000;

export interface Poll<T> {
  data: T | null;
  /** Message of the last failed refresh; the previous data stays on screen. */
  error: string | null;
  loading: boolean;
  /** Browser clock time of the last successful refresh. */
  updatedAt: Date | null;
  reload: () => void;
}

/** Fetch now and then every `ms` while the page is visible. Previous data is kept when a refresh fails. */
export function usePoll<T>(fn: () => Promise<T>, deps: unknown[], ms = POLL_MS): Poll<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);

  const run = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    fnRef.current().then(
      (d) => {
        if (mine !== seq.current) return;
        setData(d);
        setError(null);
        setUpdatedAt(new Date());
        setLoading(false);
      },
      (e: unknown) => {
        if (mine !== seq.current) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      },
    );
  }, []);

  useEffect(() => {
    run();
    const tick = (): void => {
      if (document.visibilityState === "visible") run();
    };
    const id = window.setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => {
      seq.current++;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [...deps, ms, run]);

  return { data, error, loading, updatedAt, reload: run };
}
