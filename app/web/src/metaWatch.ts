// The data line under the top bar: what the data is and when it was built, and a notice when newer data appears while
// the page is open. Read-only: it asks /api/meta again every few minutes and when the tab comes back into view.
import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import { IS_ARTIFACT } from "./env";
import { failureOf, reasonOf } from "./errors";
import { CT_ZONE, ageNote, agoText, stamp } from "./time";

export const POLL_MS = 5 * 60_000;
/** A tab-focus check is skipped when the last one was this recent. */
export const FOCUS_GAP_MS = 60_000;

export interface MetaLine {
  /** One plain sentence for the data line. */
  text: string;
  /** "replay" for fixtures, "export" for nightly data, "snapshot" for the artifact, "problem" when unread. */
  kind: "replay" | "export" | "snapshot" | "problem" | "loading";
  /** A newer data_version was seen after this page loaded; the page should offer a reload. */
  newer: string | null;
  fromCache: boolean;
}

interface MetaLike {
  mode?: unknown;
  mode_note?: unknown;
  data_as_of?: unknown;
  data_version?: unknown;
}

/** The data line for a /api/meta answer (pure; `now` for tests). Times in Central, labelled. */
export function metaText(m: MetaLike | null, artifact: boolean, now: Date = new Date()): Pick<MetaLine, "text" | "kind"> {
  if (m === null || typeof m !== "object" || typeof m.data_as_of !== "string" || typeof m.mode_note !== "string") {
    return { kind: "problem", text: "The data server answered in a form this page does not read, so what you see may be old. Nothing here is advice." };
  }
  const ago = agoText(m.data_as_of, now);
  const when = `${stamp(m.data_as_of, CT_ZONE)}${ago ? `, ${ago}` : ""}`;
  if (m.mode === "fixture_replay") {
    return { kind: "replay", text: `A replay of real 2024-25 games, not tonight's slate (fixtures built ${when}). Nothing here is advice.` };
  }
  const old = ageNote(m.data_as_of, now);
  const head = artifact ? "A saved snapshot of the nightly export; it does not update. " : "";
  return {
    kind: artifact ? "snapshot" : "export",
    text: `${head}Data as of ${when}. ${old ?? ""} ${m.mode_note}`.replace(/\s{2,}/g, " ").trim(),
  };
}

/** Has the data changed under an open page? Only when both versions are real strings and differ. */
export function versionChanged(first: string | null, next: unknown): boolean {
  return first !== null && typeof next === "string" && next !== "" && next !== first;
}

export function useMetaLine(): MetaLine {
  const [line, setLine] = useState<MetaLine>({ text: "", kind: "loading", newer: null, fromCache: false });
  useEffect(() => {
    let first: string | null = null;
    let last = 0;
    let alive = true;
    const read = (initial: boolean): void => {
      last = Date.now();
      api.meta().then(
        (r) => {
          if (!alive) return;
          const m = r.data as MetaLike | null;
          if (initial) {
            first = m && typeof m.data_version === "string" ? m.data_version : null;
            setLine({ ...metaText(m, IS_ARTIFACT), newer: null, fromCache: r.fromCache });
            return;
          }
          // A saved copy from the service worker says nothing about what the server has now.
          if (!r.fromCache && versionChanged(first, m?.data_version)) {
            const asOf = m && typeof m.data_as_of === "string" ? stamp(m.data_as_of, CT_ZONE) : "just now";
            setLine((l) => ({ ...l, newer: asOf }));
          }
        },
        (e: unknown) => {
          if (!alive || !initial) return;
          const reason = reasonOf(failureOf(e));
          setLine({
            kind: "problem",
            newer: null,
            fromCache: false,
            text:
              reason !== null
                ? `Today's data failed its checks, so it is not shown: ${reason}. Nothing here is advice.`
                : "The data server is not answering, so what you see may be old. Nothing here is advice.",
          });
        },
      );
    };
    read(true);
    if (IS_ARTIFACT) return () => {
      alive = false;
    };
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") read(false);
    }, POLL_MS);
    const onVis = (): void => {
      if (document.visibilityState === "visible" && Date.now() - last > FOCUS_GAP_MS) read(false);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);
  return line;
}
