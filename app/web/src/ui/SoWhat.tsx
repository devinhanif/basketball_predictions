import type { ComponentChildren } from "preact";

/** The one plain sentence at the top of a main page: what the stored data says, in words, before any number detail. */
export function SoWhat(props: { children: ComponentChildren }) {
  return <p class="sowhat">{props.children}</p>;
}
