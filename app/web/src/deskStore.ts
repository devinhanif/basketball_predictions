// One load of the desk export for the whole page; every desk screen reads it through useDesk().
import { useEffect, useState } from "preact/hooks";
import { getDesk, type DeskState } from "./api";
import type { Desk } from "./desk";

export type DeskHook = { kind: "loading" } | DeskState;

/** The desk when there is one; null while loading, absent or refused. */
export function deskOf(h: DeskHook): Desk | null {
  return h.kind === "ready" ? h.desk : null;
}

let cache: Promise<DeskState> | null = null;
export function loadDesk(): Promise<DeskState> {
  cache ??= getDesk().catch((): DeskState => ({ kind: "absent" }));
  return cache;
}
/** Test hook. */
export function resetDeskCache(): void {
  cache = null;
}

export function useDesk(): DeskHook {
  const [s, setS] = useState<DeskHook>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    void loadDesk().then((d) => {
      if (live) setS(d);
    });
    return () => {
      live = false;
    };
  }, []);
  return s;
}
