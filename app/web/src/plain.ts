// Plain words for things the data names in code (F22, F25). Every function here keeps the facts and the numbers;
// it only swaps an internal name for a sentence a fan can read. The exact original text is always shown one tap
// away (see Provenance in components.tsx), so nothing is hidden, only moved.
import { bookName } from "./format";

/** Which model made a forecast, in plain words. `source` is the API's model_source key. */
export function plainModel(source: string): string {
  switch (source) {
    case "avg":
      return "our main props model";
    case "fallback_stale":
    case "fallback_missing":
      return "our standard props model (the main one had no fresh forecast for this game)";
    case "fixture":
      return "our model (made without seeing these games)";
    case "legacy":
    case "postseason":
    case "non_regular_season":
      return "our standard props model";
    default:
      return "our props model";
  }
}

/** One sentence for a page or card: where the forecasts came from. */
export function plainModelSentence(source: string): string {
  return `Forecasts come from ${plainModel(source)}.`;
}

/** "Chance to play" sources, with the internal model names replaced. The original stays in the details. */
export function plainPlay(label: string): string {
  return label
    .replace(/the availability model R/g, "our availability model")
    .replace(/availability model R/g, "our availability model")
    .replace(/\bthe production model\b/g, "our standard model")
    .replace(/production's/g, "the standard model's")
    .replace(/\bR's number\b/g, "its number")
    .replace(/\bR (stored|has|had)\b/g, "it $1");
}

/** Test ids (T490, T495 ...) named in a text, in order, without repeats. */
export function testCodesIn(text: string): string[] {
  return [...new Set(text.match(/\bT\d{3}\b/g) ?? [])];
}

const BOOK_KEYS = /\b(draftkings|fanduel|betmgm|caesars|espnbet|fanatics|kalshi|novig|polymarket_us|polymarket)\b/g;

/**
 * Cleans a sentence written by the data producer for the front of a page: test ids and code names leave, book names
 * get capital letters. Numbers are never touched. Use `testCodesIn` and the original text for the details.
 */
export function plainText(text: string): string {
  return text
    .replace(/\s*\(T\d{3}\)/g, "")
    .replace(/;\s*T\d{3}(?=\))/g, "")
    .replace(/,?\s*\bT\d{3}\b/g, "")
    .replace(/rebuilt from stored out-of-fold rows/g, "rebuilt from forecasts the model made without seeing those games")
    .replace(/\bT-60 lines\b/g, "lines from an hour before tip")
    // A gap between two chances is in percentage points, never basketball points (F02).
    .replace(/(\d+(?:\.\d+)?) points (less|more) often/g, "$1 percentage points $2 often")
    .replace(/(\d+ to \d+) points (lower|higher)/g, "$1 percentage points $2")
    .replace(/\b((?:Two|Three)-leg): (\d+(?:\.\d+)?) points/g, "$1: $2 percentage points")
    .replace(/\bp_cal\b/g, "adjusted chance")
    .replace(/\bP\(over\)/g, "chance of the over")
    .replace(/\bAVG\b/g, "main-model")
    .replace(/\bCI\b/g, "range")
    .replace(/\bdate-clustered\b/g, "grouped by date")
    .replace(BOOK_KEYS, (m) => bookName(m))
    .replace(/\.\.(?!\.)/g, ".")
    .replace(/\s+([.,;)])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** Book keys written in lower case by a producer ("draftkings") get their proper names. Nothing else changes. */
export function capBooks(text: string): string {
  return text.replace(BOOK_KEYS, (m) => bookName(m));
}

/** "4 cents" style money for a ticket's result per $1 staked. */
function cents(x: number): string {
  const c = Math.round(Math.abs(x) * 100);
  return `${c.toString()} ${c === 1 ? "cent" : "cents"}`;
}

/** The verdict a ticket leads with: what $1 does on average at the book's price, if our chance is right. */
export function evVerdict(ev: number, book: string | null): string {
  const name = book ? bookName(book) : null;
  const at = name ? ` at ${name}${name.endsWith("s") ? "'" : "'s"} price` : " at the prices shown";
  if (Math.round(Math.abs(ev) * 100) === 0) return `Roughly breaks even per $1${at}`;
  return `${ev < 0 ? "Loses" : "Gains"} about ${cents(ev)} per $1${at}`;
}

/** The cautious end of the 80% range, in the same words. */
export function evCautious(low: number): string {
  if (Math.round(Math.abs(low) * 100) === 0) return "cautious end: about break-even";
  return `cautious end: ${low < 0 ? "loses" : "gains"} about ${cents(low)}`;
}

/** The one book behind every leg of a ticket, or null when the legs use more than one. */
export function ticketBook(legs: readonly { book: string }[]): string | null {
  const books = new Set(legs.map((l) => l.book));
  return books.size === 1 ? ([...books][0] ?? null) : null;
}
