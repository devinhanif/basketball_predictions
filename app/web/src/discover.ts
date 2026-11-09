// Rows for Discover's "closest look" and "On your radar": the desk's plays for the night if it has any, else the export's best-priced
// stored contracts. Every field is copied from a stored row; a missing one stays null and the screen says so.
import type { TopContract } from "./api";
import { playLabel, playsOn, type Desk } from "./desk";
import { legHref } from "./router";
import { isLegStat } from "./legTypes";

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
    matchup: p.matchup, model: p.model_prob, ask_c: p.price_cents, fee_c: p.fee_cents, edge_c: p.edge_cents, status: statusFor(p.edge_cents, p.edge_label),
    href: p.game_id && p.player_id !== null && p.stat && isLegStat(p.stat) ? legHref(p.game_id, p.player_id, p.stat) : null, gameId: p.game_id, stat: p.stat,
  };
}

function fromContract(c: TopContract): RadarRow {
  const edge = c.ev * 100;
  return {
    key: `export:${c.ticker}:${c.side}`, source: "export", name: c.player, team: c.team, playerId: c.player_id,
    market: `${c.threshold.toString()}+ ${c.stat === "fg3m" ? "threes" : c.stat}, ${c.side === "over" ? "YES" : "NO"}`, matchup: c.matchup, model: c.model_prob,
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

export function edgeSentence(r: RadarRow): string {
  if (r.model === null || r.ask_c === null || r.edge_c === null) return "No edge arithmetic is stored for this row.";
  const m = (r.model * 100).toFixed(1).replace(/\.0$/, "");
  const e = r.edge_c;
  const edge = `${e < 0 ? "-" : "+"}${Math.abs(e).toFixed(1)}c`;
  // The shared desk cut carries no fee: say so rather than print a fee of zero.
  if (r.fee_c === null) return `Model ${m}% vs ask ${Math.round(r.ask_c * 10) / 10}c; the desk's stored edge is ${edge} per $1 (its fee is not in the shared cut)`;
  return `Model ${m}% vs ask ${Math.round(r.ask_c * 10) / 10}c + fee ${Math.round(r.fee_c * 10) / 10}c = edge ${edge} per $1`;
}
