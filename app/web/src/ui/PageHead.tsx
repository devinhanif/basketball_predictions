import type { ComponentChildren } from "preact";

/**
 * The page heading block: a kicker (short, upper-case), the title, one line of description and the page's actions on the
 * right (below the text on a phone). The title is the page's h1 unless `level` says otherwise.
 */
export function PageHead(props: { kicker?: ComponentChildren; title: ComponentChildren; description?: ComponentChildren; actions?: ComponentChildren; level?: 1 | 2; class?: string; id?: string }) {
  const H = props.level === 2 ? "h2" : "h1";
  return (
    <header class={`page-heading${props.class ? ` ${props.class}` : ""}`}>
      <div class="page-heading-text">
        {props.kicker ? <div class="eyebrow">{props.kicker}</div> : null}
        <H class="page-heading-title" id={props.id}>
          {props.title}
        </H>
        {props.description ? <p class="page-heading-desc">{props.description}</p> : null}
      </div>
      {props.actions ? <div class="page-heading-actions">{props.actions}</div> : null}
    </header>
  );
}

/** A section's heading row: title and one line under it on the left, an action (link, tabs, filter) on the right. */
export function SectionHead(props: { title: ComponentChildren; sub?: ComponentChildren; action?: ComponentChildren; id?: string; level?: 2 | 3 }) {
  const H = props.level === 3 ? "h3" : "h2";
  return (
    <div class="section-head">
      <div class="section-head-text">
        <H id={props.id}>{props.title}</H>
        {props.sub ? <p>{props.sub}</p> : null}
      </div>
      {props.action ? <div class="section-head-action">{props.action}</div> : null}
    </div>
  );
}
