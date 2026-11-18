// Shared pieces of the combo screens: leg wording, the result block, the closed-game sentence. No prices anywhere.
import { Avatar } from "../Avatar";
import type { ComponentChildren } from "preact";
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
  "Every game in this file has tipped, so combo mode is closed: it only works before tip-off, so a chance is never shown next to a result.";

/** One plain sentence for a game that has tipped. */
export const closedSentence = (name: string): string => `${name} has tipped, so combo mode is closed for it: a chance is never shown next to a result.`;
/** One plain sentence for a game with no posted line in the file. */
export const noLinesSentence = (name: string): string => `${name} has no posted lines in the file yet, so there is nothing to show for it. Check back closer to tip-off.`;

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

/** The photo (or initials) of each distinct player a combo names, in leg order. The app's own Avatar, sized by the screen's CSS. */
export function Faces(props: { legs: readonly Leg[]; game: ComboGame; ctx: Ctx }) {
  const seen = new Set<number>();
  const out = [];
  for (const l of props.legs) {
    if (l.kind !== "player" || seen.has(l.player_id)) continue;
    seen.add(l.player_id);
    const p = playerOf(props.game, l.player_id);
    out.push(
      <Face key={p.player_id} name={p.name} team={teamName(props.ctx, p.team)} playerId={p.player_id} size="sm" />,
    );
  }
  return <span class="cb-faces">{out}</span>;
}

/**
 * A player's photo with the initials always underneath: the app's Avatar twice, the plain one (initials on the team colour) below and the
 * remote-headshot one on top with a transparent background. While the picture loads, or when it cannot (offline, an unknown id, the fixture,
 * the single-file artifact where no remote source exists) the initials show; a failed picture becomes the same initials. Sized by combo CSS.
 */
export function Face(props: { name: string; team: string; playerId: number; size: "sm" | "lg" }) {
  return (
    <span class="cb-av cb-face">
      <span class="cb-face-under" aria-hidden="true">
        <Avatar name={props.name} team={props.team} playerId={props.playerId} size={props.size} />
      </span>
      <span class="cb-face-over">
        <Avatar name={props.name} team={props.team} playerId={props.playerId} size={props.size} remote />
      </span>
    </span>
  );
}

/** The page-level reading notes: what the numbers are, which lines are used and how many were left out. Shown only inside "How to read this". */
export function PageNotes(props: { ctx: Ctx }) {
  const games = props.ctx.payload.games;
  let withLines = 0;
  let nStats = 0;
  let nPosted = 0;
  for (const g of games) {
    let here = 0;
    for (const p of g.players) {
      for (const e of Object.values(p.stats)) {
        nStats += 1;
        if (e.main_line !== null) here += 1;
      }
    }
    nPosted += here;
    if (here > 0) withLines += 1;
  }
  return (
    <>
      <p>
        Pure probability. Every chance on this screen is a calibrated chance from the model for a game that has not tipped, or an estimate built from those.
        The only market number used is the posted line, as a number.
      </p>
      <p>
        Only each player's posted line is used, and only when it is inside the range the calibration was checked on. {OTHER_LINES}
      </p>
      <p>
        Posted lines are in <span class="num">{withLines.toString()}</span> of <span class="num">{games.length.toString()}</span> games (<span class="num">{nPosted.toString()}</span> of{" "}
        <span class="num">{nStats.toString()}</span> player stats). A game with none says so.
      </p>
    </>
  );
}
const OTHER_LINES = "Other lines are left out until their calibration is tested.";

/** One folded disclosure, closed by default, for everything that explains rather than answers. */
export function HowToRead(props: { ctx: Ctx; children?: ComponentChildren }) {
  return (
    <details class="cb-how">
      <summary>How to read this</summary>
      <div class="cb-how-body small">
        {props.children}
        <PageNotes ctx={props.ctx} />
      </div>
    </details>
  );
}
