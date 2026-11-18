// "If the game goes like this", History tab: this season's stored counts per player, and how often the script has happened.
// NO model chance, bar or change appears here (display lock, Devin 2026-11-17); rows are sorted by player name so the order
// leaks nothing of the model. Counts are stored fields; the app counts nothing from box scores.
import { useState } from "preact/hooks";
import { api } from "../api";
import { Avatar } from "../Avatar";
import { count, pct } from "../format";
import { isLegStat } from "../legTypes";
import { legHref } from "../router";
import { Badge, Card } from "../ui";
import { useLoad } from "../useLoad";
import { teamName, type Ctx } from "./Bits";
import {
  SCRIPT_REALISED,
  SCRIPT_TITLE,
  STAT_NAME,
  historyText,
  historyView,
  histCompact,
  scriptSampleRange,
  scriptWords,
  type HistoryRow,
} from "./script";
import type { ComboGame, ScriptName } from "./types";

/** Stored past games for one leg that the app already serves (the leg page's API), on request. */
export function Trend(props: { game: ComboGame; playerId: number; stat: string; line: number; side: "over" | "under" }) {
  const [asked, setAsked] = useState(false);
  return (
    <span class="cb-trend small">
      {asked ? (
        <TrendBody {...props} />
      ) : (
        <button type="button" class="linklike" onClick={() => { setAsked(true); }}>
          Show past games
        </button>
      )}
    </span>
  );
}

function TrendBody(props: { game: ComboGame; playerId: number; stat: string; line: number; side: "over" | "under" }) {
  const { game } = props;
  const [st] = useLoad(() => api.breakdown(game.game_id, props.playerId, props.stat, props.line, props.side), [game.game_id, props.playerId, props.stat, props.side, props.line]);
  if (st.status === "loading") return <span class="muted">Looking up past games…</span>;
  if (st.status === "error" || st.data.season.n === 0) return <span class="muted">No stored past games for this leg.</span>;
  const t = st.data.season;
  return (
    <span>
      {props.side} {props.line.toString()} in <span class="num">{t.hits.toString()}</span> of <span class="num">{t.n.toString()}</span> games this season.{" "}
      {isLegStat(props.stat) ? <a href={legHref(game.game_id, props.playerId, props.stat, props.side)}>Open the leg page</a> : null}
    </span>
  );
}

/** One plain line under a count row: the count text with the muted "few games" tag. */
export function HistoryLine(props: { row: HistoryRow; script: ScriptName }) {
  const t = historyText(props.row.hist, props.script, props.row.line);
  return (
    <span class="cb-hist small">
      {t.text}
      {t.few ? <span class="muted"> (few games)</span> : null}
    </span>
  );
}

function HistoryRows(props: { ctx: Ctx; game: ComboGame; script: ScriptName; analyst: boolean }) {
  const { ctx, game, script, analyst } = props;
  const v = historyView(game);
  const [all, setAll] = useState(false);
  const cap = analyst ? 12 : 6;
  const shown = all ? v.rows : v.rows.slice(0, cap);
  return (
    <Card title="This season, player by player" class="cb-list">
      <p class="small muted">
        Games this season in which {SCRIPT_TITLE[script].toLowerCase()} happened and the player played, and how many of them went over or under the posted number. Sorted by
        player name. These are past games, not a forecast.
      </p>
      {v.rows.length === 0 ? (
        <p class="small" role="note">
          No stored counts for this game yet.
        </p>
      ) : (
        <ul class="plain cb-rows">
          {shown.map((r) => (
            <li class="cb-row cb-hrow" key={`${r.player.player_id.toString()}-${r.stat}`}>
              <span class="cb-av">
                <Avatar name={r.player.name} team={teamName(ctx, r.player.team)} playerId={r.player.player_id} size={analyst ? "sm" : "lg"} remote />
              </span>
              <div class="cb-who">
                <strong>{r.player.name}</strong>
                <span class="cb-legname">
                  posted {r.line.toString()} {STAT_NAME[r.stat] ?? r.stat}
                </span>
                {analyst ? (
                  <span class="cb-counts mono small">over: {histCompact(r.hist, script, "over") ?? "no counts"}</span>
                ) : (
                  <HistoryLine row={r} script={script} />
                )}
                <Trend game={game} playerId={r.player.player_id} stat={r.stat} line={r.line} side="over" />
              </div>
            </li>
          ))}
        </ul>
      )}
      {v.rows.length > cap ? (
        <button type="button" class="btn small" onClick={() => { setAll(!all); }}>
          {all ? "Show fewer" : `Show all ${v.rows.length.toString()}`}
        </button>
      ) : null}
      {v.missing > 0 ? <p class="small muted">{v.missing.toString()} posted line{v.missing === 1 ? " has" : "s have"} no stored counts and {v.missing === 1 ? "is" : "are"} left off this list.</p> : null}
    </Card>
  );
}

/** How often the script has happened: the contract's two seasons, this season's stored count when the file has it, and what the shift was fitted on. */
export function HistoryPanel(props: { ctx: Ctx; game: ComboGame; script: ScriptName }) {
  const { ctx, game, script } = props;
  const shared = ctx.payload.shared;
  const real = SCRIPT_REALISED[script];
  const nGames = shared.provenance?.corr_n_games;
  const own = shared.scripts.history?.[script];
  const range = scriptSampleRange(shared, script);
  return (
    <section class="panel" aria-labelledby="cb-hist-h">
      <h3 id="cb-hist-h">How often has {SCRIPT_TITLE[script].toLowerCase()} happened?</h3>
      <ul class="plain small">
        <li>
          Defined as: {scriptWords(script, game, shared)}.
        </li>
        <li>
          In the two seasons the shifts were fitted on, it happened in <span class="num">{pct(real.y2023, 1)}</span> of games in 2023 and <span class="num">{pct(real.y2024, 1)}</span> in 2024
          {nGames !== undefined ? <> (n = <span class="num">{count(nGames)}</span> games across both seasons)</> : null}.
          {script === "shootout" || script === "grind" ? " Total-points scripts move most between seasons because scoring moved." : ""}
        </li>
        {own !== undefined ? (
          <li>
            This season it has happened in <span class="num">{own.n_occurred.toString()}</span> of <span class="num">{own.n_games.toString()}</span> games.
          </li>
        ) : null}
        {range !== null ? (
          <li>
            Each leg type's shift was fitted on <span class="num">{count(range[0])}</span>
            {range[1] !== range[0] ? <> to <span class="num">{count(range[1])}</span></> : null} player-games.
          </li>
        ) : null}
        <li>
          <Badge tone="amber" icon="info" word="Exploratory" /> This script has not passed our own check as a calibrated number.
        </li>
      </ul>
    </section>
  );
}

export function HistoryView(props: { ctx: Ctx; game: ComboGame; script: ScriptName; analyst: boolean }) {
  return (
    <div class="stack">
      <HistoryPanel ctx={props.ctx} game={props.game} script={props.script} />
      <HistoryRows {...props} />
      <p class="small muted">
        Counts are this season's games against tonight's posted number, stored in the file. They are history only: this tab shows no model chance, and the model tab shows no
        count.
      </p>
    </div>
  );
}
