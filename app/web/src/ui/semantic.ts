// The one mapping from a stored label to how it looks: a colour role, an icon and a word, plus its plain meaning and the
// Key terms anchor that defines it. Nothing is told by colour alone: the icon and the word always travel with it.
import type { IconName } from "../icons";

export type EdgeLabel = "arbitrage" | "strong" | "thin" | "none";
export const EDGE_LABELS: readonly EdgeLabel[] = ["arbitrage", "strong", "thin", "none"];

export interface Look {
  /** The CSS role suffix: `tone-<role>` in kit.css. */
  role: string;
  icon: IconName;
  word: string;
  /** One plain sentence, shown in place (title) and on the Key terms page. */
  meaning: string;
  /** Anchor on the Key terms page. */
  term: string;
}

export const EDGE_LOOK: Record<EdgeLabel, Look> = {
  arbitrage: {
    role: "arb",
    icon: "swap",
    word: "Arbitrage",
    meaning: "Both sides of one game, bought on two venues, cost less than the payout after fees, with both prices confirmed by a live read and the fees verified. An unconfirmed one is only a candidate and never alerts.",
    term: "label-arbitrage",
  },
  strong: {
    role: "strong",
    icon: "up",
    word: "Strong",
    meaning: "The edge after fees is at least 3 cents per $1 contract (the desk's configured floor) and the prices were fresh.",
    term: "label-strong",
  },
  thin: {
    role: "thin",
    icon: "rise",
    word: "Thin",
    meaning: "The edge after fees is zero or positive but under the 3-cent floor, or it would be strong but the prices were stale or a venue rule is unconfirmed.",
    term: "label-thin",
  },
  none: {
    role: "none",
    icon: "none",
    word: "None",
    meaning: "The edge after fees is negative, or there was no price to measure against. The honest answer here is keep your money.",
    term: "label-none",
  },
};

/** A stored label (any case, with or without underscores) as one of the four, or null when it is something else. */
export function edgeLabelOf(raw: string | null | undefined): EdgeLabel | null {
  if (raw === null || raw === undefined) return null;
  const k = raw.trim().toLowerCase();
  if (k === "" || k === "none" || k === "no_edge" || k === "no edge") return "none";
  if (k === "arb" || k === "arbitrage") return "arbitrage";
  if (k === "strong" || k === "thin") return k;
  return null;
}

export type Compare = "better" | "worse" | "same";
export const COMPARE_LOOK: Record<Compare, { icon: IconName; word: string }> = {
  better: { icon: "up", word: "better" },
  worse: { icon: "dn", word: "worse" },
  same: { icon: "same", word: "same" },
};

/**
 * Which way a stored signed difference points, for display only. `lowerIsBetter` for losses such as CRPS or Brier.
 * A difference inside `tie` either way is "same" (the stored rule's own floor, passed in; 0 by default).
 */
export function compareOf(diff: number, lowerIsBetter: boolean, tie = 0): Compare {
  if (!Number.isFinite(diff) || Math.abs(diff) <= tie) return "same";
  const up = diff > 0;
  return up !== lowerIsBetter ? "better" : "worse";
}

export const SIDE_LOOK: Record<"over" | "under", { icon: IconName; word: string }> = {
  over: { icon: "up", word: "Over" },
  under: { icon: "dn", word: "Under" },
};
