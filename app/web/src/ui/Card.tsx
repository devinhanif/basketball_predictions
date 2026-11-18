import type { ComponentChildren } from "preact";

/**
 * A section of a page (`.panel`). `tone` marks a verdict (accent rule) or an error (loss rule). `variant` gives the
 * Jarvis finishes: `feature` (the purple hero card, one per page at most) or `header` (the quieter gradient card).
 * With `actions`, the kicker and title sit in a header row with the actions on the right.
 */
export function Card(props: {
  title?: ComponentChildren;
  kicker?: string;
  tone?: "verdict" | "error" | "empty";
  variant?: "feature" | "header";
  actions?: ComponentChildren;
  flat?: boolean;
  id?: string;
  class?: string;
  children: ComponentChildren;
}) {
  const cls = ["panel", props.tone ?? "", props.variant ? `card-${props.variant}` : "", props.flat ? "flat" : "", props.class ?? ""].filter(Boolean).join(" ");
  const head = (
    <>
      {props.kicker ? <div class="kicker">{props.kicker}</div> : null}
      {props.title ? <h2>{props.title}</h2> : null}
    </>
  );
  return (
    <section class={cls} id={props.id}>
      {props.actions ? (
        <div class="card-head">
          <div class="card-head-text">{head}</div>
          <div class="card-head-actions">{props.actions}</div>
        </div>
      ) : (
        head
      )}
      {props.children}
    </section>
  );
}
