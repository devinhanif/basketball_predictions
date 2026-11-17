import type { ComponentChildren } from "preact";
import { Icon } from "../icons";
import { ProblemPanel, type ProblemProps } from "../Problem";
import { Skeleton } from "./Skeleton";
import { STATE_LOOK, type StateKind } from "./logic";

/**
 * One family for the states a screen can be in besides "here is the data": loading, empty, stale, partial, error and
 * reconnecting. Each says what it is in words (an icon and a word as well as a colour), says why in a sentence, and
 * offers a way on when there is one. `error` is the app's existing failure screen (Problem) and needs its code.
 */
export function StateBlock(props: {
  kind: StateKind;
  title?: ComponentChildren;
  children?: ComponentChildren;
  action?: ComponentChildren;
  /** Required for `error`: the registry code and the rest of the Problem screen's fields. */
  problem?: ProblemProps;
  /** Inline: a one-line strip inside a section instead of a block. */
  inline?: boolean;
  /** Skeleton rows while loading. */
  lines?: number;
}) {
  if (props.kind === "error" && props.problem) return <ProblemPanel level={2} {...props.problem} />;
  const l = STATE_LOOK[props.kind];
  const busy = props.kind === "loading" || props.kind === "reconnecting";
  return (
    <div class={`state state-${props.kind} tone-${l.tone}${props.inline ? " inline" : ""}`} role={l.role === "note" ? "note" : l.role} aria-busy={busy ? "true" : undefined} data-state={props.kind}>
      <div class="state-head">
        <span class="state-mark" aria-hidden="true">
          <Icon name={l.icon} />
        </span>
        <span class="state-word">{l.word}</span>
      </div>
      <div class="state-body">
        <strong class="state-title">{props.title ?? l.title}</strong>
        <div class="state-text">{props.children ?? l.meaning}</div>
        {props.action ? <div class="state-action">{props.action}</div> : null}
      </div>
      {props.kind === "loading" && !props.inline ? <Skeleton lines={props.lines ?? 2} /> : null}
    </div>
  );
}
