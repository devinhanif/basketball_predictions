import type { ComponentChildren } from "preact";
import { CERTAINTY_LABEL, certaintyOf, type Desk, type DeskObservation } from "./desk";
import { useDesk, deskOf } from "./deskStore";
import { Loading } from "./components";

/** Runs a desk screen once the desk has loaded; says plainly when there is none (a normal state, not an error). */
export function DeskGate(props: { title: string; children: (desk: Desk) => ComponentChildren }) {
  const h = useDesk();
  if (h.kind === "loading") return <Loading />;
  const d = deskOf(h);
  if (d) return <>{props.children(d)}</>;
  return (
    <div class="stack">
      <header class="page-head">
        <h1>{props.title}</h1>
      </header>
      <section class="panel empty" aria-label="Desk not available">
        <h2>{h.kind === "refused" ? "The desk export did not pass its checks" : "No desk data in this build"}</h2>
        <p class="muted">
          {h.kind === "refused"
            ? `We are not showing it. Reason: ${h.detail}`
            : "The Jarvis Desk export is not part of this data: the public API does not serve one, or none has been written yet. Nothing is missing from the forecasts; this screen simply has no source."}
        </p>
        <a class="btn" href="#/">
          Back to Discover
        </a>
      </section>
    </div>
  );
}

export function SectionNote({ desk, k }: { desk: Desk; k: string }) {
  const n = desk.section_notes[k];
  return n ? <p class="small muted section-note">{n}</p> : null;
}

/** The label that tells a reported fact from an inference from a correction. Colour is never the only signal. */
export function CertaintyChip({ o }: { o: DeskObservation }) {
  const c = certaintyOf(o);
  return <span class={`chip certainty ${c}`}>{CERTAINTY_LABEL[c]}</span>;
}

export function Skeleton() {
  return <Loading />;
}

/** A page of a long list: shows the first `step`, then more on request. */
export function pageOf<T>(xs: readonly T[], shown: number): T[] {
  return xs.slice(0, shown);
}
