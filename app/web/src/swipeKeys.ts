import type { Pick } from "./ledger";

/**
 * Keyboard answers for the Swipe deck (F12). Keys count only when the deck surface itself has focus:
 * not when focus is on a button, a link, the "Stats and line slider" summary or a form control, where the
 * same keys already mean something. ArrowDown is left to the browser (it scrolls); skip is Space or S.
 */
export function swipeKeyChoice(key: string, onDeckSurface: boolean): Pick["side"] | null {
  if (!onDeckSurface) return null;
  switch (key) {
    case "ArrowRight":
      return "over";
    case "ArrowLeft":
      return "under";
    case " ":
    case "Spacebar":
    case "s":
    case "S":
      return "skip";
    default:
      return null;
  }
}
