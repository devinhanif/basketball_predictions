import type { ComponentChildren } from "preact";
import { Icon, type IconName } from "../icons";

const ICON: Record<string, IconName> = { info: "info", notice: "flag", fresh: "reset" };

/** A full-width line under the top bar: info (as-of), notice (something to know) or fresh (newer data exists). */
export function Banner(props: { tone?: "info" | "notice" | "fresh"; role?: "status" | "note" | "alert"; children: ComponentChildren; action?: ComponentChildren; class?: string }) {
  const tone = props.tone ?? "notice";
  return (
    <div class={`kbanner tone-${tone}${props.class ? ` ${props.class}` : ""}`} role={props.role ?? "status"}>
      <Icon name={ICON[tone] ?? "info"} />
      <span class="kbanner-text">{props.children}</span>
      {props.action ?? null}
    </div>
  );
}
