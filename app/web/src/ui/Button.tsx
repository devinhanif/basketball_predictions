import type { ButtonHTMLAttributes, ComponentChildren } from "preact";

type Props = Omit<ButtonHTMLAttributes, "class" | "type" | "children"> & {
  kind?: "primary" | "plain" | "quiet";
  wide?: boolean;
  type?: "button" | "submit";
  children: ComponentChildren;
  class?: string;
};

/** The one button: `.btn`, 44 px tall, primary for the page's main action only. */
export function Button({ kind = "plain", wide, type = "button", children, class: extra, ...rest }: Props) {
  const cls = ["btn", kind === "primary" ? "primary" : kind === "quiet" ? "quiet" : "", wide ? "wide" : "", extra ?? ""].filter(Boolean).join(" ");
  return (
    <button type={type} class={cls} {...rest}>
      {children}
    </button>
  );
}
