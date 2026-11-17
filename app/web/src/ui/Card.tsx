import type { ComponentChildren } from "preact";

/** A section of a page (`.panel`). `tone` marks a verdict (accent rule) or an error (loss rule). */
export function Card(props: { title?: ComponentChildren; kicker?: string; tone?: "verdict" | "error" | "empty"; flat?: boolean; id?: string; class?: string; children: ComponentChildren }) {
  const cls = ["panel", props.tone ?? "", props.flat ? "flat" : "", props.class ?? ""].filter(Boolean).join(" ");
  return (
    <section class={cls} id={props.id}>
      {props.kicker ? <div class="kicker">{props.kicker}</div> : null}
      {props.title ? <h2>{props.title}</h2> : null}
      {props.children}
    </section>
  );
}
