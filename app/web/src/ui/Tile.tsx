import type { ComponentChildren } from "preact";

/**
 * One stored number with what it is: label, value (already formatted), unit, its n, and a one-line note.
 * The value is a string so nothing is computed here.
 */
export function Tile(props: { label: string; value: string; unit?: string; n?: string | null; note?: ComponentChildren; tone?: "model" | "mine" | "base"; class?: string }) {
  return (
    <div class={["tile", props.tone ? `tone-${props.tone}` : "", props.class ?? ""].filter(Boolean).join(" ")}>
      <div class="tile-v num">
        {props.value}
        {props.unit ? <span class="tile-u">{props.unit}</span> : null}
      </div>
      <div class="tile-l">{props.label}</div>
      {props.n ? <div class="tile-n muted num">{props.n}</div> : null}
      {props.note ? <div class="tile-note small muted">{props.note}</div> : null}
    </div>
  );
}
