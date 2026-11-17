import { useCallback, useEffect, useState } from "preact/hooks";
import type { Loaded } from "./api";
import { failureOf, type ApiFailure } from "./errors";

export type LoadState<T> =
  | { status: "loading" }
  | { status: "error"; message: string; failure: ApiFailure }
  | { status: "ready"; data: T; fromCache: boolean };

export function useLoad<T>(fn: () => Promise<Loaded<T>>, deps: unknown[]): [LoadState<T>, () => void] {
  const [state, setState] = useState<LoadState<T>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    fn().then(
      (r) => {
        if (live) setState({ status: "ready", data: r.data, fromCache: r.fromCache });
      },
      (e: unknown) => {
        if (live) setState({ status: "error", message: e instanceof Error ? e.message : String(e), failure: failureOf(e) });
      },
    );
    return () => {
      live = false;
    };
  }, [...deps, tick]);
  const reload = useCallback(() => {
    setTick((t) => t + 1);
  }, []);
  return [state, reload];
}
