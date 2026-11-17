import { Icon, type IconName } from "../icons";
import { termHref } from "../terms";
import { COMPARE_LOOK, EDGE_LOOK, SIDE_LOOK, edgeLabelOf, type Compare } from "./semantic";

/** A generic badge: a tone, an icon and a word. Never colour alone. */
export function Badge(props: { tone: string; icon: IconName; word: string; title?: string }) {
  return (
    <span class={`kbadge tone-${props.tone}`} title={props.title}>
      <Icon name={props.icon} />
      <span>{props.word}</span>
    </span>
  );
}

/**
 * A stored edge label (arbitrage / strong / thin / none) as colour + icon + word, with its plain meaning in place and a
 * link to Key terms. A label outside the four is shown verbatim in grey, never guessed into one of them.
 */
export function EdgeBadge(props: { label: string | null | undefined; link?: boolean }) {
  // No stored label reads as "none", as the desk ledger always showed it.
  const k = props.label === null || props.label === undefined ? "none" : edgeLabelOf(props.label);
  if (k === null) {
    const raw = (props.label ?? "").replace(/_/g, " ");
    return <Badge tone="none" icon="info" word={raw === "" ? "none" : raw} title="A label outside the four the desk defines; shown as stored." />;
  }
  const look = EDGE_LOOK[k];
  const badge = <Badge tone={look.role} icon={look.icon} word={look.word} title={look.meaning} />;
  return props.link === false ? (
    badge
  ) : (
    <a class="kbadge-link" href={termHref(look.term)} aria-label={`${look.word} edge: ${look.meaning} Opens Key terms.`}>
      {badge}
    </a>
  );
}

/** Better / worse / same as icon + word in its tone. */
export function CompareBadge(props: { which: Compare; what?: string }) {
  const l = COMPARE_LOOK[props.which];
  return <Badge tone={props.which} icon={l.icon} word={props.what ? `${l.word} ${props.what}` : l.word} />;
}

/** Over / under as icon + word (no colour difference between the two sides). */
export function SideBadge(props: { side: "over" | "under" }) {
  const l = SIDE_LOOK[props.side];
  return <Badge tone="side" icon={l.icon} word={l.word} />;
}
