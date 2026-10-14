import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "./api";
import { settle, type Pick, type Settled } from "./ledger";
import type { ResultRow } from "./types";

export type ResultsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; rows: ResultRow[] };

/** Fetches the real results for every slate date in the ledger. */
export function useResults(dates: string[]): ResultsState {
  const [state, setState] = useState<ResultsState>({ status: "loading" });
  const key = dates.join(",");
  useEffect(() => {
    let live = true;
    if (dates.length === 0) {
      setState({ status: "ready", rows: [] });
      return;
    }
    Promise.all(dates.map((d) => api.results(d))).then(
      (rs) => {
        if (live) setState({ status: "ready", rows: rs.flatMap((r) => r.data.rows) });
      },
      (e: unknown) => {
        if (live) setState({ status: "error", message: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      live = false;
    };
  }, [key]);
  return state;
}

/** Settles the ledger against the results. */
export function useSettled(picks: Pick[]): { results: ResultsState; settled: Settled[]; open: Pick[] } {
  const dates = useMemo(() => [...new Set(picks.map((p) => p.slate_date))].sort(), [picks]);
  const results = useResults(dates);
  const { settled, open } = useMemo(
    () => (results.status === "ready" ? settle(picks, results.rows) : { settled: [] as Settled[], open: [] as Pick[] }),
    [picks, results],
  );
  return { results, settled, open };
}
