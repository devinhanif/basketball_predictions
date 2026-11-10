// 'If a teammate sits': words for what the API sends (api/scenarios.py). The API already limits what leaves the
// server to what the test supports (T526 / T526-a, 2023-25 history): points direction per teammate and one
// team-level number, rebounds direction only ("uncertain size"). Nothing here computes a size, a chance or a
// price, and nothing here is advice. Assists and threes are never sent, so they cannot be shown.

export type Direction = "up" | "down" | "flat";

export interface TeamBump {
  direction: Direction;
  total: number;
  per_player: number | null;
  n_likely: number;
}

export interface ScenarioTeammate {
  player_id: number;
  name: string;
  likely_plays: boolean;
  pts: Direction;
  reb: Direction | null;
}

export interface ScenarioSitter {
  player_id: number;
  name: string;
  team_id: number;
  side: "home" | "away" | null;
  status: string;
  pts: TeamBump;
  reb: { direction: Direction; uncertain_size: boolean } | null;
  teammates: ScenarioTeammate[];
}

export interface ScenariosView {
  game_id: string;
  available: boolean;
  reason: string | null;
  made_at: string | null;
  evidence: string[];
  not_modelled: string[];
  sitters: ScenarioSitter[];
}

const DIR_WORD: Record<Direction, string> = { up: "higher", down: "lower", flat: "about the same" };
export const dirWord = (d: Direction): string => DIR_WORD[d];
export const arrow = (d: Direction): string => (d === "up" ? "↑" : d === "down" ? "↓" : "→");

/** Sitters on this player's team (he is among the teammates; he cannot be his own sitter), most disruptive first. */
export function sittersFor(view: ScenariosView, playerId: number): ScenarioSitter[] {
  if (!view.available) return [];
  return view.sitters.filter((s) => s.player_id !== playerId && s.teammates.some((t) => t.player_id === playerId));
}

const STATUS_WORD: Record<string, string> = {
  available: "listed available",
  probable: "listed probable",
  questionable: "listed questionable",
  doubtful: "listed doubtful",
  unlisted: "not on the injury report",
};
export const statusWord = (s: string): string => STATUS_WORD[s] ?? `listed ${s}`;

/** Points, team level: "If X sits, teammates' points forecasts rise by about 2.1 in total (about 0.7 per likely player)." */
export function ptsSentence(s: ScenarioSitter): string {
  const t = s.pts;
  if (t.direction === "flat") return `If ${s.name} sits, teammates' points forecasts barely move.`;
  const verb = t.direction === "up" ? "rise" : "fall";
  const total = Math.abs(t.total).toFixed(1);
  const per =
    t.per_player !== null && t.n_likely > 0
      ? ` (about ${Math.abs(t.per_player).toFixed(1)} per teammate likely to play)`
      : "";
  return `If ${s.name} sits, teammates' points forecasts ${verb} by about ${total} in total${per}.`;
}

/** Rebounds: direction only, always with the size caveat. */
export function rebSentence(s: ScenarioSitter): string | null {
  if (s.reb === null) return null;
  const d = s.reb.direction;
  const what = d === "up" ? "tend to rise" : d === "down" ? "tend to fall" : "barely move";
  return `Rebounds: teammates' forecasts ${what} (uncertain size).`;
}

/** For the card's own player: direction only, no per-player size. */
export function playerSentence(s: ScenarioSitter, playerId: number, name: string): string | null {
  const me = s.teammates.find((t) => t.player_id === playerId);
  if (!me) return null;
  const reb = me.reb === null ? "" : `; rebounds ${dirWord(me.reb)} (uncertain size)`;
  return `${name}'s forecast: points ${dirWord(me.pts)}${reb}.`;
}

/** The evidence label shown with every scenario, in plain words. */
export const EVIDENCE_HEADLINE = "Tested on 2023-25 history: helps for points; rebounds direction only.";
