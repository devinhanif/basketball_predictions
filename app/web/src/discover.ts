// Rows for Discover's "closest look" and "On your radar": the desk's plays for the night if it has any, else the export's best-priced
// stored contracts. Every field is copied from a stored row; a missing one stays null and the screen says so.
import type { TopContract } from "./api";
import { playLabel, playsOn, type Desk } from "./desk";
import { legHref } from "./router";
import { isLegStat } from "./legTypes";
import { legWords } from "./hedge";
import { STAT_LABEL, type Stat } from "./types";

export interface RadarRow {
  key: string;
  source: "desk" | "export";
  name: string;
  team: string | null;
  playerId: number | null;
  /** "3+ threes, YES" */
  market: string;
  matchup: string | null;
  /** Model chance, 0-1. */
  model: number | null;
  /** The desk's sharp-book fair price, 0-1 (desk rows only). The desk's edge is built from this, not from the model. */
  fair: number | null;
  ask_c: number | null;
  fee_c: number | null;
  /** Edge after fees in cents; null when none is stored. */
  edge_c: number | null;
  status: string;
  href: string | null;
  gameId: string | null;
  stat: string | null;
}

export function statusFor(edge: number | null, label: string | null): string {
  if (edge === null) return label ?? "No edge stored";
  const sign = edge > 0 ? "Positive after fees" : edge < 0 ? "Negative after fees" : "Flat after fees";
  return label && label !== "none" ? `${label[0]?.toUpperCase() ?? ""}${label.slice(1)} edge · ${sign.toLowerCase()}` : sign;
}

function fromPlay(p: Desk["plays"][number]): RadarRow {
  // A game-result play has no player: its own description is the name.
  const name = p.player_name ?? p.leg_desc ?? "Unnamed play";
  return {
    key: `desk:${p.play_id}`, source: "desk", name, team: p.team, playerId: p.player_id, market: p.player_name ? playLabel(p).replace(`${p.player_name}: `, "").replace(`${p.player_name} `, "") : "game result",
    matchup: p.matchup, model: p.model_prob, fair: p.fair_prob, ask_c: p.price_cents, fee_c: p.fee_cents, edge_c: p.edge_cents, status: statusFor(p.edge_cents, p.edge_label),
    href: p.game_id && p.player_id !== null && p.stat && isLegStat(p.stat) ? legHref(p.game_id, p.player_id, p.stat) : null, gameId: p.game_id, stat: p.stat,
  };
}

function fromContract(c: TopContract): RadarRow {
  const edge = c.ev * 100;
  return {
    key: `export:${c.ticker}:${c.side}`, source: "export", name: c.player, team: c.team, playerId: c.player_id,
    market: legWords(c.side === "over" ? "yes" : "no", c.threshold, (STAT_LABEL[c.stat as Stat] as string | undefined)?.toLowerCase() ?? c.stat), matchup: c.matchup, model: c.model_prob, fair: null,
    ask_c: c.price * 100, fee_c: c.fees * 100, edge_c: edge, status: statusFor(edge, null), href: isLegStat(c.stat) ? legHref(c.game_id, c.player_id, c.stat, c.side) : null, gameId: c.game_id, stat: c.stat,
  };
}

/** Rows for the night shown: desk plays that night (best edge first), else the export's contracts when they are for that night. */
export function radarRows(desk: Desk | null, date: string, top: { report_date: string | null; contracts: TopContract[] } | null, limit = 8): RadarRow[] {
  if (desk) {
    const plays = playsOn(desk, date);
    if (plays.length > 0) return plays.slice(0, limit).map(fromPlay);
  }
  if (top && top.report_date === date) return top.contracts.slice(0, limit).map(fromContract);
  return [];
}

/** The watch key for a row. */
export function watchKey(r: RadarRow): string {
  return r.key.slice(0, 90);
}

/**
 * The edge arithmetic, said with what it is made of. A desk row: JARVIS's edge is the sharp book's fair price minus the ask
 * minus the fee (the model weight is 0 and the model is not an input). An export row: the engine's model chance minus the
 * ask minus the fee. Only stored fields; the one subtraction shown for the shared cut (fair minus ask) is plain arithmetic
 * on two stored numbers.
 */
export function edgeSentence(r: RadarRow): string {
  if (r.ask_c === null || r.edge_c === null) return "No edge arithmetic is stored for this row.";
  const c = (x: number): string => `${(Math.round(x * 10) / 10).toString()}c`;
  const pctText = (x: number): string => `${(x * 100).toFixed(1).replace(/\.0$/, "")}%`;
  const edge = `${r.edge_c < 0 ? "-" : "+"}${Math.abs(r.edge_c).toFixed(1)}c`;
  if (r.source === "desk") {
    if (r.fair === null) return `Stored edge ${edge} per $1 after fees (the fair price is not stored for this row).`;
    if (r.fee_c === null) {
      const before = r.fair * 100 - r.ask_c;
      return `Fair price ${pctText(r.fair)} (sharp book) - ask ${c(r.ask_c)} = ${before < 0 ? "-" : "+"}${Math.abs(before).toFixed(1)}c before fee; stored edge after fee ${edge}`;
    }
    return `Fair price ${pctText(r.fair)} (sharp book) - ask ${c(r.ask_c)} - fee ${c(r.fee_c)} = edge ${edge} per $1`;
  }
  if (r.model === null) return "No edge arithmetic is stored for this row.";
  return `Model ${pctText(r.model)} vs ask ${c(r.ask_c)} + fee ${c(r.fee_c ?? 0)} = edge ${edge} per $1`;
}
