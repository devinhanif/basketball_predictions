import { Icon, type IconName } from "../icons";
import { termHref } from "../terms";
import { FRESH_LOOK, PROV_LOOK, freshness, provenanceOf } from "./logic";
import { COMPARE_LOOK, EDGE_LOOK, SIDE_LOOK, edgeLabelOf, type Compare } from "./semantic";

/** A generic badge: a tone, an icon and a word. Never colour alone. */
export function Badge(props: { tone: string; icon: IconName; word: string; title?: string; class?: string }) {
  return (
    <span class={`kbadge tone-${props.tone}${props.class ? ` ${props.class}` : ""}`} title={props.title}>
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

/**
 * Where a number came from: live / delayed / manual / sample, as icon + word with its meaning in place. A source outside
 * the four is shown verbatim in grey, never guessed. Pass the stored source; this component infers nothing.
 */
export function ProvenanceBadge(props: { source: string | null | undefined }) {
  const k = provenanceOf(props.source);
  if (k === null) return <Badge tone="none" icon="info" word={props.source ? props.source : "source not reported"} title="A source the app does not define; shown as stored." />;
  const l = PROV_LOOK[k];
  return <Badge tone={l.tone} icon={l.icon} word={l.word} title={l.meaning} class={`prov prov-${k}`} />;
}

/**
 * How old a stored time is, against the caller's limit in hours: "Fresh · 4 h ago", "Old · 3 days ago" or "Time not
 * reported". `label` names the thing ("Pinnacle quote"). The age is always written, so nothing rests on colour.
 */
export function FreshnessBadge(props: { at: string | null | undefined; oldAfterHours: number; label?: string; now?: Date }) {
  const f = freshness(props.at, props.oldAfterHours, props.now);
  const l = FRESH_LOOK[f.state];
  const word = [props.label, f.ago ? `${l.word.toLowerCase()} · ${f.ago}` : l.word.toLowerCase()].filter(Boolean).join(": ");
  return <Badge tone={l.tone} icon={l.icon} word={word.charAt(0).toUpperCase() + word.slice(1)} title={l.meaning} class={`fresh fresh-${f.state}`} />;
}
