// Pure logic of the combo screen: what a script does to each calibrated main-line leg, the plain sentence, the stored
// history lines, the calibrated-range filter and the tip rule. No UI imports, no I/O, no prices.
import { instantMs } from "../time";
import { evaluateGameLegs, scriptDefinitions, scriptPrior, statEntries, survAt } from "./engine";
import { SCRIPTS, type ComboGame, type ComboPlayer, type ComboShared, type PlayerLeg, type ScriptName, type StatGrid } from "./types";

export const STAT_NAME: Record<string, string> = { pts: "points", reb: "rebounds", ast: "assists", fg3m: "threes" };
export const STAT_SHORT: Record<string, string> = { pts: "pts", reb: "reb", ast: "ast", fg3m: "3PM" };

export const SCRIPT_TITLE: Record<ScriptName, string> = { blowout: "A blowout", close: "A close game", shootout: "A shootout", grind: "A grind" };
/** The plural a count line uses ("In blowouts this season"). */
export const SCRIPT_PLURAL: Record<ScriptName, string> = { blowout: "blowouts", close: "close games", shootout: "shootouts", grind: "grinds" };
/** "If the game goes like this" wording, one per script. */
export const SCRIPT_IF: Record<ScriptName, string> = { blowout: "is a blowout", close: "is a close game", shootout: "is a shootout", grind: "is a grind" };

/**
 * How often each script has happened (the contract, section 6.3 and 7: share of games, 2023 and 2024, the games the
 * calibration was fitted on). Fixed historical facts quoted from the contract, not computed here and not from tonight's
 * payload; `corr_n_games` in the payload gives the games behind both seasons. A shootout or grind threshold moves
 * with the scoring environment, which is why those shares differ most between seasons.
 */
export const SCRIPT_REALISED: Record<ScriptName, { y2023: number; y2024: number }> = {
  blowout: { y2023: 0.328, y2024: 0.344 },
  close: { y2023: 0.235, y2024: 0.252 },
  shootout: { y2023: 0.221, y2024: 0.286 },
  grind: { y2023: 0.311, y2024: 0.227 },
};

/** The plain definition of a script for this game, from the payload's own thresholds. */
export function scriptWords(script: ScriptName, game: ComboGame, shared: ComboShared): string {
  const d = shared.scripts.definitions;
  const defs = scriptDefinitions(game, shared);
  switch (script) {
    case "blowout":
      return `decided by ${d.blowout_abs_margin.toString()} or more`;
    case "close":
      return `decided by ${d.close_abs_margin.toString()} or fewer`;
    case "shootout":
      return `${(defs["shootout"]?.min_total ?? 0).toString()} or more total points`;
    case "grind":
      return `${(defs["grind"]?.max_total ?? 0).toString()} or fewer total points`;
  }
}

/** A script's definition without a game's numbers (for combos across games). */
export function scriptGeneric(script: ScriptName, shared: ComboShared): string {
  const d = shared.scripts.definitions;
  switch (script) {
    case "blowout":
      return `decided by ${d.blowout_abs_margin.toString()} or more`;
    case "close":
      return `decided by ${d.close_abs_margin.toString()} or fewer`;
    case "shootout":
      return `${d.total_delta.toString()} or more points above the expected total`;
    case "grind":
      return `${d.total_delta.toString()} or more points below the expected total`;
  }
}

/**
 * THE restriction of this version: every picker offers each player's posted MAIN line only (over or under), for every framing.
 * The line calibration was fitted on lines the books posted; applied to an off-main rung it flattens the ladder, so such a rung is
 * not a trustworthy calibrated number even inside `cal_k`. The engine port stays complete; this is a screen rule.
 */
export const MAIN_LINE_ONLY: boolean = ((): boolean => true)();
export const OTHER_LINES_SENTENCE = "Other lines are left out until their calibration is tested.";

/** The lines of a stat the screen may offer: the posted main line when it is inside the calibrated range (all in-range rungs only if MAIN_LINE_ONLY were off). */
export function offerableLines(grid: StatGrid): number[] {
  const ml = grid.main_line;
  if (MAIN_LINE_ONLY) return ml !== null && lineInRange(grid, ml) ? [ml] : [];
  const out: number[] = [];
  for (let k = Math.max(1, grid.cal_k[0]); k <= grid.cal_k[1]; k++) {
    const p = survAt(grid, k);
    if (p > 0 && p < 1) out.push(k - 0.5);
  }
  return out;
}

/** How many players' stats in a game carry a posted line at all (the game is "thin" when none does). */
export function postedLineCount(game: ComboGame): number {
  let n = 0;
  for (const p of game.players) for (const [, g] of statEntries(p)) if (g.main_line !== null) n += 1;
  return n;
}

/** Is a main-line leg's rung inside the stat's calibrated range? "over x.5" and "under x.5" both sit on rung x + 1. */
export function lineInRange(grid: Pick<StatGrid, "cal_k">, line: number): boolean {
  const k = Math.floor(line) + 1;
  return k >= grid.cal_k[0] && k <= grid.cal_k[1];
}

/** Has the game tipped on this clock? Combo mode is closed for it (a chance is never put beside a result). */
export function hasTipped(game: Pick<ComboGame, "tip_utc">, clockUtc: string): boolean {
  const tip = instantMs(game.tip_utc);
  const now = instantMs(clockUtc);
  if (Number.isNaN(tip) || Number.isNaN(now)) return true; // an unreadable time is closed, never open
  return now >= tip;
}

export interface Hist {
  n_all: number | null;
  k_all: number | null;
  /** per script: [n, k] */
  by: Partial<Record<ScriptName, [number, number]>>;
}

const wholeNonNeg = (x: unknown): x is number => typeof x === "number" && Number.isInteger(x) && x >= 0;

/** The stored season counts, read tolerantly: absent, partial, extra keys and malformed pairs all give "nothing", never a guess. */
export function readHist(raw: unknown): Hist | null {
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, unknown>;
  const by: Hist["by"] = {};
  for (const s of SCRIPTS) {
    const v = o[s];
    if (Array.isArray(v) && v.length === 2 && wholeNonNeg(v[0]) && wholeNonNeg(v[1]) && v[1] <= v[0]) by[s] = [v[0], v[1]];
  }
  const nAll = wholeNonNeg(o["n_all"]) ? o["n_all"] : null;
  const kAll = wholeNonNeg(o["k_all"]) && nAll !== null && o["k_all"] <= nAll ? o["k_all"] : null;
  if (nAll === null && Object.keys(by).length === 0) return null;
  return { n_all: nAll, k_all: kAll, by };
}

export interface HistLine {
  /** "In blowouts this season: over 24.5 in 2 of 3 games." or "no blowouts yet this season". */
  text: string;
  /** "All games: 7 of 12." */
  all: string | null;
  few: boolean;
}

/** The side's count from the stored over count: under = n minus k (arithmetic on two stored numbers, no counting). */
const sideCount = (side: "over" | "under", n: number, k: number): number => (side === "over" ? k : n - k);

/** One plain line from the stored pair. Both numbers of a pair always appear; n under 5 says "few games"; n of 0 says so. */
export function histLine(hist: Hist | null, script: ScriptName, side: "over" | "under", line: number): HistLine | null {
  if (hist === null) return null;
  const all =
    hist.n_all !== null && hist.k_all !== null
      ? `All games this season: ${sideCount(side, hist.n_all, hist.k_all).toString()} of ${hist.n_all.toString()}.`
      : null;
  const pair = hist.by[script];
  if (pair === undefined) return all === null ? null : { text: "", all, few: false };
  const [n, k] = pair;
  if (n === 0) return { text: `No ${SCRIPT_PLURAL[script]} yet this season.`, all, few: false };
  return {
    text: `In ${SCRIPT_PLURAL[script]} this season: ${side} ${line.toString()} in ${sideCount(side, n, k).toString()} of ${n.toString()} games.`,
    all,
    few: n < 5,
  };
}

export interface ScriptRow {
  player: ComboPlayer;
  stat: string;
  side: "over" | "under";
  line: number;
  p_now: number;
  p_script: number;
  change: number;
  hist: HistLine | null;
  /** Terminal form: "blow 2/4  all 6/12". */
  hist_short: string | null;
}

export interface ScriptView {
  more: ScriptRow[];
  less: ScriptRow[];
  /** main-line legs left out because the line is outside the stat's calibrated range */
  dropped: number;
  /** main-line legs offered (both sides counted once per player and stat) */
  offered: number;
  prior: number;
  starters: GroupMove | null;
  bench: GroupMove | null;
}

export interface GroupMove {
  n: number;
  before: number;
  after: number;
}

const mean = (xs: readonly number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;

/**
 * What a script does to every calibrated main-line leg of one game: the chance as it stands, the chance if the script
 * happens, and the change, over and under both, split into legs that become more likely and legs that become less
 * likely, biggest change first. Only lines inside the stat's calibrated range; `dropped` says how many were left out.
 * The history lines are stored counts shown beside; they take no part in the numbers or the order.
 */
export function scriptView(game: ComboGame, shared: ComboShared, script: ScriptName): ScriptView {
  const rows: ScriptRow[] = [];
  let dropped = 0;
  let offered = 0;
  const startOver: [number, number][] = [];
  const benchOver: [number, number][] = [];
  for (const player of game.players) {
    for (const [stat, grid] of statEntries(player)) {
      const ml = grid.main_line;
      if (ml === null) continue;
      if (!lineInRange(grid, ml)) {
        dropped += 1;
        continue;
      }
      offered += 1;
      const hist = readHist(grid.hist);
      for (const side of ["over", "under"] as const) {
        const leg: PlayerLeg = { kind: "player", game_id: game.game_id, player_id: player.player_id, stat, line: ml, side };
        const now = evaluateGameLegs([leg], game, shared).p_joint;
        const sc = evaluateGameLegs([leg], game, shared, script).p_joint;
        rows.push({ player, stat, side, line: ml, p_now: now, p_script: sc, change: sc - now, hist: histLine(hist, script, side, ml), hist_short: histCompact(hist, script, side) });
        if (side === "over") (player.starter ? startOver : benchOver).push([now, sc]);
      }
    }
  }
  const move = (xs: [number, number][]): GroupMove | null => (xs.length === 0 ? null : { n: xs.length, before: mean(xs.map((x) => x[0])), after: mean(xs.map((x) => x[1])) });
  const byChange = (a: ScriptRow, b: ScriptRow): number => Math.abs(b.change) - Math.abs(a.change) || a.player.player_id - b.player.player_id || (a.stat < b.stat ? -1 : 1);
  return {
    more: rows.filter((r) => r.change > 0).sort(byChange),
    less: rows.filter((r) => r.change < 0).sort(byChange),
    dropped,
    offered,
    prior: scriptPrior(script, game, shared),
    starters: move(startOver),
    bench: move(benchOver),
  };
}

const pc = (p: number): string => `${(p * 100).toFixed(1)}%`;

/** One sentence per script, built from the moves above: the direction words come from the signs of the numbers. */
export function scriptSentence(script: ScriptName, v: ScriptView): string | null {
  const part = (who: string, m: GroupMove | null): string | null => {
    if (m === null) return null;
    const d = m.after - m.before;
    const word = Math.abs(d) < 0.005 ? "barely move" : d < 0 ? "get harder" : "get easier";
    return `${who} overs ${word} (${pc(m.before)} to ${pc(m.after)} on average over ${m.n.toString()} lines)`;
  };
  const a = part("starters'", v.starters);
  const b = part("bench", v.bench);
  const parts = [a, b].filter((x): x is string => x !== null);
  if (parts.length === 0) return null;
  return `If this ${SCRIPT_IF[script]}: ${parts.join("; ")}.`;
}

/** The smallest and largest n among a script's parameter rows: how many player-games each shift was fitted on. */
export function scriptSampleRange(shared: ComboShared, script: ScriptName): [number, number] | null {
  const ns = shared.scripts.params.filter((r) => r[0] === script).map((r) => r[5]);
  return ns.length === 0 ? null : [Math.min(...ns), Math.max(...ns)];
}

export function statusWord(shared: ComboShared, script: ScriptName): string {
  const s = shared.scripts.status[script];
  const word = typeof s === "string" ? s : s?.status;
  return word === "calibrated" ? "calibrated" : "exploratory";
}

/** "line opened 23.5, now 24.5" from the optional stored opening line; a number only, never interpreted. */
export function lineMove(grid: StatGrid): string | null {
  const open = grid.main_line_open;
  const now = grid.main_line;
  if (open === null || open === undefined || now === null) return null;
  return open === now ? `line has stayed at ${now.toString()}` : `line opened ${open.toString()}, now ${now.toString()}`;
}

/** The compact count for a terminal row: "blow 2/4  all 6/12" (this side's count over games). Nothing is hidden: n of 0 says "none yet". */
export const SCRIPT_SHORT: Record<ScriptName, string> = { blowout: "blow", close: "close", shootout: "shoot", grind: "grind" };

export function histCompact(hist: Hist | null, script: ScriptName, side: "over" | "under"): string | null {
  if (hist === null) return null;
  const parts: string[] = [];
  const pair = hist.by[script];
  if (pair !== undefined) {
    parts.push(pair[0] === 0 ? `${SCRIPT_SHORT[script]} none yet` : `${SCRIPT_SHORT[script]} ${sideCount(side, pair[0], pair[1]).toString()}/${pair[0].toString()}${pair[0] < 5 ? " few" : ""}`);
  }
  if (hist.n_all !== null && hist.k_all !== null) parts.push(`all ${sideCount(side, hist.n_all, hist.k_all).toString()}/${hist.n_all.toString()}`);
  return parts.length === 0 ? null : parts.join("  ");
}
