// Shared pieces of the combo screens: leg wording, the result block, the closed-game sentence. No prices anywhere.
import { pct } from "../format";
import { ESTIMATE_LABEL, playerOf } from "./engine";
import { STAT_NAME } from "./script";
import type { ComboGame, ComboPayload, ComboResult, Leg, PlayerLeg } from "./types";

export interface Ctx {
  payload: ComboPayload;
  clock: string;
  /** team id -> abbreviation, from the night's slate when the app has it */
  teams: ReadonlyMap<number, string>;
}

export const CLOSED_SENTENCE =
  "This game has already tipped, so combo mode is closed for it. It only works before tip-off, so a chance is never shown next to a result.";

export const OUT_OF_RANGE_NOTE =
  "Lines outside the range the calibration was checked on are left out of this version, because those chances are extrapolation.";

/** NBA team ids (stable since 2010) to abbreviations: the fallback when the night has no slate in the app. */
const TEAM_ABBR: Record<number, string> = {
  1610612737: "ATL", 1610612738: "BOS", 1610612739: "CLE", 1610612740: "NOP", 1610612741: "CHI", 1610612742: "DAL",
  1610612743: "DEN", 1610612744: "GSW", 1610612745: "HOU", 1610612746: "LAC", 1610612747: "LAL", 1610612748: "MIA",
  1610612749: "MIL", 1610612750: "MIN", 1610612751: "BKN", 1610612752: "NYK", 1610612753: "ORL", 1610612754: "IND",
  1610612755: "PHI", 1610612756: "PHX", 1610612757: "POR", 1610612758: "SAC", 1610612759: "SAS", 1610612760: "OKC",
  1610612761: "TOR", 1610612762: "UTA", 1610612763: "MEM", 1610612764: "WAS", 1610612765: "DET", 1610612766: "CHA",
};
export const teamName = (ctx: Ctx, id: number): string => ctx.teams.get(id) ?? TEAM_ABBR[id] ?? `team ${id.toString()}`;
export const matchup = (ctx: Ctx, g: ComboGame): string => `${teamName(ctx, g.away_team)} at ${teamName(ctx, g.home_team)}`;

export function legWords(leg: Leg, g: ComboGame): string {
  if (leg.kind !== "player") return leg.kind === "total" ? `total ${leg.side} ${leg.line.toString()}` : "margin leg";
  const name = playerOf(g, leg.player_id).name;
  return `${name} ${leg.side} ${leg.line.toString()} ${STAT_NAME[leg.stat] ?? leg.stat}`;
}

export function pc(p: number, d = 1): string {
  return pct(p, d);
}

/** The combined result of a combo, in plain words; each number is from the engine's result and nothing else. */
export function ResultBlock(props: { r: ComboResult; sameGame: boolean }) {
  const { r } = props;
  const ent = r.n_entities;
  return (
    <div class="cb-result">
      <p class="cb-big num">
        {pc(r.p_joint)}
        {r.method === "qmc" && r.range_2se !== null ? (
          <span class="cb-pm small muted"> plus or minus {pc(2 * r.se, 2)} (2 se; range {pc(r.range_2se[0])} to {pc(r.range_2se[1])})</span>
        ) : null}
      </p>
      <p class="small">
        chance every leg hits, if every player named plays.
        {r.lift !== null && ent > 1 ? (
          <>
            {" "}
            If the legs were unrelated it would be <span class="num">{pc(r.p_independent)}</span>
            {props.sameGame ? (
              <>
                , so they move together by a factor of <span class="num">{r.lift.toFixed(2)}</span>.
              </>
            ) : (
              <>
                . Different games are treated as independent (measured correlation about 0), so no lift is shown across games.
              </>
            )}
          </>
        ) : null}
      </p>
      <p class="small">
        {r.p_all_play === null ? (
          <>
            Chance everyone plays: <strong>unknown</strong> (at least one player has no stored chance to play), so the chance with a
            non-playing player counted as a miss is unknown too.
          </>
        ) : (
          <>
            Chance everyone plays: <span class="num">{pc(r.p_all_play)}</span>. Counting a player who does not play as a miss, the chance
            all legs hit is <span class="num">{pc(r.p_joint_incl_dnp ?? 0)}</span>.
          </>
        )}
      </p>
      {r.label !== null ? (
        <p class="small cb-estimate">
          <strong>{r.label}.</strong> Only two-leg combos have been scored against past results; this one comes from the same model but is
          not checked.
        </p>
      ) : null}
      {r.above_tested_range.some(Boolean) ? (
        <p class="small cb-warn">
          <strong>Above the tested range, likely too high:</strong> at least one leg has a chance above what past results supported.
          The number is shown as it is, not capped.
        </p>
      ) : null}
      {r.repair_t > 0 ? (
        <p class="small muted">The correlations were nudged toward zero by {r.repair_t.toFixed(3)} to make them consistent.</p>
      ) : null}
      <p class="small muted">A higher chance means the legs move together or are each likely. It does not say the combo is a good idea.</p>
    </div>
  );
}

export function legKey(l: PlayerLeg): string {
  return `${l.game_id}|${l.player_id.toString()}|${l.stat}|${l.side}|${l.line.toString()}`;
}

export const ESTIMATE = ESTIMATE_LABEL;

/** Product of the named players' stored chance to play (each once); null when any is unknown. Same rule as the engine. */
export function allPlay(legs: readonly Leg[], g: ComboGame): number | null {
  const ids = new Set(legs.flatMap((l) => (l.kind === "player" ? [l.player_id] : [])));
  let p = 1;
  for (const id of ids) {
    const pp = playerOf(g, id).p_play;
    if (pp === null) return null;
    p *= pp;
  }
  return p;
}

/** "Everyone plays 91.2%; with a non-player counted as a miss, 20.1%" or "unknown". */
export function PlayLine(props: { legs: readonly Leg[]; game: ComboGame; pJoint: number }) {
  const a = allPlay(props.legs, props.game);
  return (
    <p class="small muted">
      {a === null ? (
        <>Chance everyone plays: unknown.</>
      ) : (
        <>
          Chance everyone plays <span class="num">{pc(a)}</span>; counting a player who does not play as a miss, <span class="num">{pc(a * props.pJoint)}</span>.
        </>
      )}
    </p>
  );
}
