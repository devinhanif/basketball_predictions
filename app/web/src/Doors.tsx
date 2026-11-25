// Doors: a coloured outlined box that links to another job's screen. It is drawn in the DESTINATION's colour and carries
// the destination's name in words, so colour is never the only signal. A door is never built for a screen that does not
// exist, and never carries a number the screen did not already have.
import type { ComponentChildren } from "preact";
import { Icon } from "./icons";
import { JOBS, type Job } from "./jobs";

/** A small door: swatch, destination name, one plain line, an arrow. */
export function Door(props: { to: Job; href: string; children: ComponentChildren; label?: string }) {
  return (
    <a class="door" data-to={props.to} href={props.href} aria-label={props.label}>
      <span class="door-sw" aria-hidden="true" />
      <span class="door-name">{JOBS[props.to].tab}</span>
      <span class="door-text">{props.children}</span>
      <span class="door-go" aria-hidden="true">
        &rsaquo;
      </span>
    </a>
  );
}

/** A labelled group of doors ("Go deeper on this game"). */
export function DoorGroup(props: { label: string; children: ComponentChildren; class?: string }) {
  return (
    <section class={`door-group${props.class ? ` ${props.class}` : ""}`} aria-label={props.label}>
      <div class="door-group-label">{props.label}</div>
      {props.children}
    </section>
  );
}

/** A large home door: an icon block in the job's fill, the job's name, one sentence, and one real fact when there is one. */
export function BigDoor(props: { job: Job; href?: string; name: string; sentence: string; fact?: string | null }) {
  const def = JOBS[props.job];
  return (
    <a class="home-door" data-to={props.job} href={props.href ?? def.href}>
      <span class="home-door-icon" aria-hidden="true">
        <Icon name={def.icon} />
      </span>
      <span class="home-door-body">
        <span class="home-door-name">{props.name}</span>
        <span>{props.sentence}</span>
        {props.fact ? <span class="home-door-fact">{props.fact}</span> : null}
      </span>
    </a>
  );
}
