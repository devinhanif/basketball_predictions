import type { ComponentChildren } from "preact";
import { CERTAINTY_LABEL, certaintyOf, type Desk, type DeskObservation } from "./desk";
import { useDesk, deskOf } from "./deskStore";
import { Loading } from "./components";
import { ageNote, deskStamp } from "./format";

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
        <h2>{h.kind === "refused" ? "We are holding this screen back" : "Nothing to show here yet"}</h2>
        <p class="muted">
          {h.kind === "refused"
            ? `Its data did not pass our safety checks, so we are not showing it. Reason: ${h.detail}`
            : "The desk has not published its numbers yet. The game forecasts and your picks are not affected."}
        </p>
        <a class="btn" href="#/">
          Back to Discover
        </a>
      </section>
    </div>
  );
}

/** Says when the desk file was built and, once it is old, how old. The desk data is no newer than the file. */
export function DeskAge({ desk }: { desk: Desk }) {
  const old = ageNote(desk.built_at);
  return (
    <span class="desk-age">
      Desk as of {deskStamp(desk.built_at)}.{old ? ` ${old}` : ""}
    </span>
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
