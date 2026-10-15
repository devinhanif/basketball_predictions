// Small pure helpers for the role facts. They only format numbers the API already computed.
import type { BeyondStat, OnOffWindow, Per36, Rate, Steadiness } from "./roleTypes";

/** A signed one-decimal number with a real minus sign, for gaps between two rates: "+3.4", "-4.7". */
export function signed(x: number, digits = 1): string {
  const body = Math.abs(x).toFixed(digits);
  if (Number(body) === 0) return body;
  return `${x < 0 ? "-" : "+"}${body}`;
}

/** "+11.0" for a net rating, or a dash when the side has no possessions. */
export function netText(r: Rate): string {
  return r.net === null ? "none" : signed(r.net);
}

/** "3,214" */
export function thousands(n: number): string {
  return n.toLocaleString("en-US");
}

/** "1,420 possessions". */
export function possText(r: Rate): string {
  return `${thousands(r.poss)} possessions`;
}

/** "-4.5 to +11.2": the approximate 95% range around an on/off gap, or null without one. */
export function rangeOf(w: OnOffWindow): string | null {
  if (w.ci_lo === null || w.ci_hi === null) return null;
  return `${signed(w.ci_lo)} to ${signed(w.ci_hi)}`;
}

/** "gap +3.4, 95% range -4.5 to +11.2", or just the gap when there is no interval. */
export function gapText(w: OnOffWindow): string | null {
  if (w.diff === null) return null;
  if (w.ci_lo === null || w.ci_hi === null) return `gap ${signed(w.diff)}`;
  return `gap ${signed(w.diff)}, 95% range ${signed(w.ci_lo)} to ${signed(w.ci_hi)}`;
}

/** The sample marker printed with every on/off number. */
export function onoffN(w: OnOffWindow): string {
  const base = `${thousands(w.games)} ${w.games === 1 ? "game" : "games"}`;
  return w.small ? `${base}, small sample` : base;
}

/** "n = 4, small sample". */
export function per36N(p: Per36): string {
  return p.small ? `n = ${p.n.toString()}, small sample` : `n = ${p.n.toString()}`;
}

/** "10.0 pts, 5.0 reb, 2.0 ast per 36", or null with no games. */
export function per36Text(p: Per36): string | null {
  if (p.pts36 === null || p.reb36 === null || p.ast36 === null) return null;
  return `${p.pts36.toFixed(1)} pts, ${p.reb36.toFixed(1)} reb, ${p.ast36.toFixed(1)} ast per 36`;
}

/** The compact badge text: always carries n, never a bare label. */
export function badgeText(s: Steadiness): string {
  if (s.label === "Limited data") return `Limited data, ${s.n.toString()} ${s.n === 1 ? "game" : "games"}`;
  return `${s.label}, last ${s.n.toString()} games`;
}

/** The headline number for one stat: "+0.14". Threes say "no measurable effect" in words, never a bare 0. */
export function extraText(s: BeyondStat): string {
  if (s.no_effect) return "no measurable effect";
  if (s.no_carry_large) return "no measurable extra";
  if (s.expected_extra === null) return "none";
  return signed(s.expected_extra, 2);
}

/** The raw history for one stat, with its n: "past games +3.0 a game, n = 15". */
export function pastText(s: BeyondStat, nWithout: number): string {
  if (s.past_gap === null) return "no games to compare";
  return `past games ${signed(s.past_gap)} a game, n = ${nWithout.toString()}`;
}

/** "about 1 in 27 of the past gap showed up again", or "" when none did. */
export function carriedText(s: BeyondStat): string {
  if (s.no_carry_large) return "none of a gap this big measurably showed up again";
  if (s.one_in === null) return "";
  return `about 1 in ${s.one_in.toString()} of the past gap showed up again${s.large_gap ? " (for gaps this big)" : ""}`;
}
