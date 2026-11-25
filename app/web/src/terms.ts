// Key terms: one or two plain sentences each, seeded from the project glossary. `market` terms are about prices and only
// appear in the Market lens (the Learn lens shows no price words). Each has an anchor: link with termHref(id).
import { IS_ARTIFACT } from "./env";
import { LENS_SAY } from "./Lens";
import { EDGE_LABELS, EDGE_LOOK } from "./ui/semantic";

export interface Term {
  id: string;
  term: string;
  /** Other words a visitor might search for. */
  also?: string;
  says: string;
  market?: boolean;
}

const BASE: Term[] = [
  { id: "keep-your-money", term: "Keep your money", also: "what should I bet", says: "The app's standing answer to \"what should I bet?\". Until the live forward record proves skill, nothing here is recommended, and a night with nothing worth taking is a result, not an error." },
  { id: "forecast", term: "Forecast", also: "range distribution", says: "A range of outcomes with how likely each is, not a single promised number. The typical night is the middle; the band shows how far either side most nights land." },
  { id: "t-minus", term: "T-60 / T-30 / T-15", also: "tip-off minutes before", says: "Minutes before the real scheduled tip-off. A forecast stamped T-60 was made an hour before tip, using only what was known then." },
  { id: "made-at", term: "made_at", also: "stamp forecast time", says: "When a forecast was made. It must be before tip; a forecast stamped after tip is refused." },
  { id: "as-of", term: "As-of / known-at", also: "knowable leakage", says: "Only information that was available at that moment is used. Every stored fact says when it became knowable, so nothing from the future leaks in." },
  { id: "p-play", term: "p_play", also: "chance to play injury", says: "The chance a player plays at all tonight. Where one is stored, the page shows it with the sentence naming its source." },
  { id: "quantile-grid", term: "Quantile grid", also: "percentile bars tail", says: "A forecast stored as a list of percentiles. The bars and chances on a page are read from it; beyond its ends the page writes 95%+ or under 5% instead of guessing a tail it does not hold." },
  { id: "calibrated", term: "Calibrated", also: "model only", says: "Over many cases, things given a 60% chance happened about 60% of the time. Where a calibrated chance exists only that is shown; otherwise the chance is marked \"model only\"." },
  { id: "crps", term: "CRPS / pinball loss", also: "score distribution", says: "Scores for a whole forecast range, lower is better. CRPS rewards putting weight close to what happened; pinball loss scores each percentile. For counts like points the whole-number version is used." },
  { id: "brier", term: "Brier score / log loss", also: "score probability", says: "Scores for a stated chance of a yes-or-no event, lower is better. Brier is the squared gap between the chance and what happened; log loss punishes a confident miss much harder." },
  { id: "n-interval", term: "n and clustered interval", also: "sample size confidence range", says: "n is how many cases a number rests on. The interval is the range the true value plausibly sits in, computed with whole games (or dates) as the units, because players in one game move together. A claim without both is not a claim." },
  { id: "holdout", term: "Holdout", also: "frozen season 2025", says: "A season kept untouched for one final test. Season 2025 is the frozen holdout here; every touch is logged before it happens." },
  { id: "prereg", term: "Pre-registered / frozen rule", also: "prereg decision rule", says: "The rule that decides whether a model passes (what is scored, the bar, the interval, which seasons) is written and committed before any result exists, and is never changed after." },
  { id: "replay-oracle", term: "Replay oracle", also: "fingerprint refactor", says: "A fingerprint of a fixed replay of 20 past dates. If a code change leaves it identical, the change did not alter any forecast." },
  { id: "shadow", term: "Shadow model", also: "logged forward", says: "A model logged forward every night that does not drive what the app shows, so its record builds without risk." },
  { id: "replay", term: "Replay", also: "fixture past games", says: "Real past games shown as they stood before tip. A replay is labelled as such and is never tonight's slate." },
];

const MARKET: Term[] = [
  { id: "clv", term: "CLV (closing-line value)", also: "close", says: "The middle of the closing price minus the price paid. Positive means the market later moved toward the side taken.", market: true },
  { id: "de-vig", term: "De-vig / no-vig", also: "fair price margin", says: "A book's prices on both sides add up to more than 100%; removing that margin gives the market's fair chances.", market: true },
  { id: "edge-after-fees", term: "Edge after fees", also: "EV expected value", says: "The model's chance minus the price paid, less the venue's fee, per $1 contract. When none is positive the page says so: no positive edge found.", market: true },
  { id: "dnp-rule", term: "DNP rule (Kalshi props)", also: "did not play withdrawal", says: "On Kalshi player props, a player withdrawn before tip settles at the last fair price rather than as a loss.", market: true },
  ...EDGE_LABELS.map((l) => ({ id: EDGE_LOOK[l].term, term: `Label: ${l}`, also: "edge label", says: EDGE_LOOK[l].meaning, market: true })),
];

/** Every term. The lens entry is built from the lens switch's own sentences so the two never disagree. */
export function allTerms(): Term[] {
  const lenses: Term = {
    id: "lenses",
    term: "Lenses",
    also: "market side hidden shown",
    says: `${LENS_SAY.base} The market side can be shown from the profile menu.`,
  };
  const marketLens: Term = { id: "lens-market", term: "Market lens", also: "lenses", says: LENS_SAY.market, market: true };
  return [...BASE, lenses, ...MARKET, marketLens];
}

/** The terms a lens may show: Learn and Explore leave the market terms out. */
export function termsFor(market: boolean): Term[] {
  return allTerms().filter((t) => market || t.market !== true);
}

/** Terms matching a search: every word must appear in the name, the other words or the definition. */
export function searchTerms(list: readonly Term[], q: string): Term[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...list];
  return list.filter((t) => {
    const hay = `${t.term} ${t.also ?? ""} ${t.says}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** Link to one term on the Key terms page. */
export function termHref(id: string): string {
  return `#/terms?t=${encodeURIComponent(id)}`;
}

/** Link to the page itself. */
export function termsHref(): string {
  return IS_ARTIFACT ? "#terms" : "#/terms";
}
