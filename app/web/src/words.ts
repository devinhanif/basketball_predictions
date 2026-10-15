// Plain-language helpers. Every function here turns a stored number into a sentence;
// none of them invents a number. The exact percentages are always printed next to the words.
import type { Stat } from "./types";

/** "about 6 times in 10" for a probability. */
export function inTen(p: number, extrapolated = false): string {
  if (extrapolated && p >= 0.9) return "nearly every time";
  if (extrapolated && p <= 0.1) return "very rarely";
  const n = Math.round(p * 10);
  if (n >= 10) return "nearly every time";
  if (n <= 0) return "almost never";
  if (n === 5) return "about half the time";
  if (n === 1) return "about 1 time in 10";
  return `about ${n} times in 10`;
}

/** The smallest whole number that counts as "over" a line like 24.5 (or 25). */
export function overFrom(line: number): number {
  return Math.floor(line) + 1;
}

/** The largest whole number that counts as "under" a line like 24.5 (or 25). */
export function underTo(line: number): number {
  return Number.isInteger(line) ? line - 1 : Math.floor(line);
}

const NOUN: Record<Stat, [string, string]> = {
  pts: ["point", "points"],
  reb: ["rebound", "rebounds"],
  ast: ["assist", "assists"],
  fg3m: ["three", "threes"],
};

const VERB: Record<Stat, string> = { pts: "scores", reb: "grabs", ast: "gets", fg3m: "hits" };
const VERB_PAST: Record<Stat, string> = { pts: "scored", reb: "grabbed", ast: "had", fg3m: "hit" };

function noun(stat: Stat, n: number): string {
  return NOUN[stat][n === 1 ? 0 : 1];
}

/** "25 or more points" / "24 or fewer points" / "no assists". */
export function sideText(stat: Stat, line: number, side: "over" | "under"): string {
  if (side === "over") {
    const n = overFrom(line);
    return `${n.toString()} or more ${noun(stat, 2)}`;
  }
  const n = underTo(line);
  if (n <= 0) return `no ${noun(stat, 2)}`;
  return `${n.toString()} or fewer ${noun(stat, 2)}`;
}

/** "scores 25 or more points" / "grabs 8 or more rebounds". */
export function doesText(stat: Stat, line: number, side: "over" | "under"): string {
  const n = side === "over" ? overFrom(line) : underTo(line);
  if (side === "under" && n <= 0) return `${VERB[stat]} no ${noun(stat, 2)}`;
  return `${VERB[stat]} ${sideText(stat, line, side)}`;
}

/** "Jayson Tatum scores 29 or more points about 4 times in 10". */
export function chanceSentence(
  name: string,
  stat: Stat,
  line: number,
  side: "over" | "under",
  p: number,
  extrapolated = false,
): string {
  return `${name} ${doesText(stat, line, side)} ${inTen(p, extrapolated)}`;
}

/** What a player did: "scored 28" / "grabbed 8 rebounds" / "had 6 assists" / "hit 1 three". */
export function didText(stat: Stat, actual: number): string {
  if (stat === "pts") return `scored ${actual.toString()}`;
  return `${VERB_PAST[stat]} ${actual.toString()} ${noun(stat, actual)}`;
}

/** The stat name in a single word, for headings. */
export function statWord(stat: Stat): string {
  return NOUN[stat][1];
}

/** Win-chance sentence for one forecast. p is the home team's chance. */
export function winSentence(homeP: number, home: string, away: string): string {
  if (Math.abs(homeP - 0.5) < 0.05) return "Close to a coin flip";
  const homeFav = homeP > 0.5;
  const favP = homeFav ? homeP : 1 - homeP;
  return `${homeFav ? home : away} win ${inTen(favP)}`;
}

/** What to call the marked number on a distribution bar. Never "book line" for a line no book set. */
export function lineMarkLabel(source: "posted" | "reference"): string {
  return source === "posted" ? "line" : "our reference line";
}

/**
 * What to lead with when the line is only our own reference point (F07): the forecast itself. A chance of
 * going over a line set at the middle of the forecast is about 50% by construction, so it says nothing.
 */
export function rangeSentence(stat: Stat, typical: string, lo: string, hi: string): string {
  return `A typical night is ${typical} ${NOUN[stat][1]}; half of his nights land between ${lo} and ${hi}`;
}
