// "If the game goes like this": pick a script, see which calibrated main-line legs become more or less likely.
import { useMemo, useState } from "preact/hooks";
import { api } from "../api";
import { count, pct } from "../format";
import { legHref } from "../router";
import { isLegStat } from "../legTypes";
import { useLoad } from "../useLoad";
import { Avatar } from "../Avatar";
import { useMode } from "../prefs";
import { Badge, Card } from "../ui";
import { Bar } from "./Bar";
import { OUT_OF_RANGE_NOTE, matchup, pc, teamName, type Ctx } from "./Bits";
import {
  SCRIPT_REALISED,
  SCRIPT_SHORT,
  SCRIPT_TITLE,
  STAT_NAME,
  lineMove,
  scriptSampleRange,
  scriptSentence,
  scriptView,
  scriptWords,
  statusWord,
  type ScriptRow,
} from "./script";
import { SCRIPTS, type ComboGame, type ScriptName } from "./types";
import { statEntries } from "./engine";

/** Stored history for one leg that the app already serves (the leg page's API), on request: season hits of games, a link. */
function Trend(props: { game: ComboGame; row: ScriptRow }) {
  const [asked, setAsked] = useState(false);
  return (
    <span class="cb-trend small">
      {asked ? <TrendBody {...props} /> : (
        <button type="button" class="linklike" onClick={() => { setAsked(true); }}>
          Show past games
        </button>
      )}
    </span>
  );
}

function TrendBody(props: { game: ComboGame; row: ScriptRow }) {
  const { game, row } = props;
  const [st] = useLoad(
    () => api.breakdown(game.game_id, row.player.player_id, row.stat, row.line, row.side),
    [game.game_id, row.player.player_id, row.stat, row.side, row.line],
  );
  if (st.status === "loading") return <span class="muted">Looking up past games…</span>;
  if (st.status === "error" || st.data.season.n === 0) return <span class="muted">No stored past games for this leg.</span>;
  const t = st.data.season;
  return (
    <span>
      {row.side} {row.line.toString()} in <span class="num">{t.hits.toString()}</span> of <span class="num">{t.n.toString()}</span> games this season.{" "}
      {isLegStat(row.stat) ? <a href={legHref(game.game_id, row.player.player_id, row.stat, row.side)}>Open the leg page</a> : null}
    </span>
  );
}

function Row(props: { ctx: Ctx; game: ComboGame; row: ScriptRow; script: ScriptName; kind: "more" | "less"; analyst: boolean }) {
  const { row, game, ctx, analyst } = props;
  const grid = statEntries(row.player).find((e) => e[0] === row.stat)?.[1];
  const move = grid ? lineMove(grid) : null;
  const d = row.change * 100;
  const sign = d >= 0 ? "+" : "−";
  return (
    <li class="cb-row">
      <span class="cb-av">
        <Avatar name={row.player.name} team={teamName(ctx, row.player.team)} playerId={row.player.player_id} size={analyst ? "sm" : "lg"} remote />
      </span>
      <div class="cb-who">
        <strong>{row.player.name}</strong>
        <span class="cb-legname">
          {row.side} {row.line.toString()} {STAT_NAME[row.stat] ?? row.stat}
        </span>
        {analyst ? (
          row.hist_short !== null ? <span class="cb-counts mono small">{row.hist_short}</span> : null
        ) : row.hist !== null ? (
          <span class="cb-hist small">
            {row.hist.text !== "" ? <span>{row.hist.text} </span> : null}
            {row.hist.few ? <span class="muted">(few games) </span> : null}
            {row.hist.all !== null ? <span class="muted">{row.hist.all}</span> : null}
          </span>
        ) : null}
        {move !== null ? <span class="cb-move small muted">Posted {move}.</span> : null}
        <Trend game={game} row={row} />
      </div>
      <Bar now={row.p_now} script={row.p_script} scriptName="it happens" />
      <div class="cb-end num">
        <b class="cb-end-main">{pc(row.p_script)}</b>
        <span class={`cb-delta ${props.kind}`}>
          {sign}
          {Math.abs(d).toFixed(1)} pts
        </span>
        <span class="muted small">was {pc(row.p_now)}</span>
      </div>
    </li>
  );
}

function RowList(props: { ctx: Ctx; title: string; sub: string; rows: ScriptRow[]; game: ComboGame; script: ScriptName; kind: "more" | "less"; analyst: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? props.rows : props.rows.slice(0, 6);
  return (
    <Card title={props.title} class={`cb-list cb-${props.kind}`}>
      <p class="small muted">{props.sub}</p>
      {props.rows.length === 0 ? (
        <p class="small">None.</p>
      ) : (
        <ul class="plain cb-rows">
          {shown.map((r) => (
            <Row key={`${r.player.player_id.toString()}-${r.stat}-${r.side}`} ctx={props.ctx} game={props.game} row={r} script={props.script} kind={props.kind} analyst={props.analyst} />
          ))}
        </ul>
      )}
      {props.rows.length > 6 ? (
        <button type="button" class="btn small" onClick={() => { setAll(!all); }}>
          {all ? "Show fewer" : `Show all ${props.rows.length.toString()}`}
        </button>
      ) : null}
    </Card>
  );
}

export function ScriptTab(props: { ctx: Ctx; game: ComboGame }) {
  const { ctx, game } = props;
  const shared = ctx.payload.shared;
  const analyst = useMode() === "analyst";
  const [script, setScript] = useState<ScriptName>("blowout");
  const view = useMemo(() => scriptView(game, shared, script), [game, shared, script]);
  const sentence = scriptSentence(script, view);
  const range = scriptSampleRange(shared, script);
  const real = SCRIPT_REALISED[script];
  const nGames = shared.provenance?.corr_n_games;
  const own = shared.scripts.history?.[script];
  const prior = useMemo(() => Object.fromEntries(SCRIPTS.map((s) => [s, scriptView(game, shared, s).prior])) as Record<ScriptName, number>, [game, shared]);
  const hasMain = game.players.some((p) => statEntries(p).some(([, g]) => g.main_line !== null));
  return (
    <div class="stack">
      <section aria-labelledby="cb-sc-h" class="panel">
        <h2 id="cb-sc-h">If the game goes like this</h2>
        <p class="small muted">Pick a way the game could go. Below, the lines that get more likely and the lines that get less likely if it does.</p>
        {analyst ? (
          <div class="cb-scroll">
            <table class="cb-script-table">
              <caption class="cb-cmd">
                combo --game {matchup(ctx, game)} --script {script} --lines posted --calibrated
              </caption>
              <thead>
                <tr>
                  <th scope="col">script</th>
                  <th scope="col">rule</th>
                  <th scope="col" class="num">tonight</th>
                  <th scope="col" class="num">2023 / 2024</th>
                  <th scope="col" class="num">this season</th>
                  <th scope="col">status</th>
                </tr>
              </thead>
              <tbody>
                {SCRIPTS.map((s) => {
                  const h = shared.scripts.history?.[s];
                  return (
                    <tr key={s} class={script === s ? "on" : ""}>
                      <th scope="row">
                        <button type="button" class="cb-trow" aria-pressed={script === s} onClick={() => { setScript(s); }}>
                          {SCRIPT_SHORT[s]}
                        </button>
                      </th>
                      <td>{scriptWords(s, game, shared)}</td>
                      <td class="num">{pc(prior[s])}</td>
                      <td class="num">
                        {pct(SCRIPT_REALISED[s].y2023, 1)} / {pct(SCRIPT_REALISED[s].y2024, 1)}
                      </td>
                      <td class="num">{h !== undefined ? `${h.n_occurred.toString()}/${h.n_games.toString()}` : "n/a"}</td>
                      <td>{statusWord(shared, s)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
        <div class="cb-scripts" role="group" aria-label="Game script">
          {SCRIPTS.map((s) => (
            <button key={s} type="button" class="cb-script" aria-pressed={script === s} onClick={() => { setScript(s); }}>
              <span class="cb-script-title">{SCRIPT_TITLE[s]}</span>
              <span class="small">{scriptWords(s, game, shared)}</span>
              <span class="small num">about {pc(prior[s], 0)} tonight</span>
              <span class="cb-tag">{statusWord(shared, s)}</span>
            </button>
          ))}
        </div>
        )}
      </section>

      <section class="panel" aria-labelledby="cb-how-h">
        <h3 id="cb-how-h">How often does {SCRIPT_TITLE[script].toLowerCase()} happen?</h3>
        <ul class="plain small">
          <li>
            Tonight: the model gives it <span class="num">{pc(view.prior)}</span> for this game ({scriptWords(script, game, shared)}), from its expected margin and total.
          </li>
          <li>
            In the two seasons this was fitted on, it happened in <span class="num">{pct(real.y2023, 1)}</span> of games in 2023 and{" "}
            <span class="num">{pct(real.y2024, 1)}</span> in 2024
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
              The shift for each leg type was fitted on <span class="num">{count(range[0])}</span>
              {range[1] !== range[0] ? <> to <span class="num">{count(range[1])}</span></> : null} player-games.
            </li>
          ) : null}
          <li>
            <Badge tone="amber" icon="info" word="Exploratory" /> The effect of this script has not passed our own check as a calibrated number. Treat the
            changes below as estimates.
          </li>
        </ul>
      </section>

      {hasMain ? (
        <>
          {sentence !== null ? <p class="cb-sentence">{sentence}</p> : null}
          <p class="small muted cb-legend">
            Bars run from 30% to 70%. Grey tick: the chance as it stands. Square: the chance if it happens. The band is green when the chance rises and red when it
            falls; the signed change is always written beside it. Outside the scale a bar sits at the edge and says "off scale".
          </p>
          <div class="cb-two">
            <RowList ctx={ctx} analyst={analyst} title="More likely if it happens" sub="Chance goes up. Biggest change first." rows={view.more} game={game} script={script} kind="more" />
            <RowList ctx={ctx} analyst={analyst} title="Less likely if it happens" sub="Chance goes down. Biggest change first." rows={view.less} game={game} script={script} kind="less" />
          </div>
          <p class="small muted">
            Every line has two sides: when one side gets more likely the other gets less likely, so each line appears once in each list. Only lines at
            the posted number are shown, and only chances that were checked against past results. {OUT_OF_RANGE_NOTE}
            {view.dropped > 0 ? ` ${view.dropped.toString()} of ${(view.dropped + view.offered).toString()} posted lines in this game were left out for that reason.` : " None of this game's posted lines were left out."}{" "}
            Past counts, where shown, are this season's games against the posted number; they sit beside the model's chance and are not part of it.
          </p>
        </>
      ) : (
        <p class="panel">This game has no posted lines in the file, so there is nothing to show for it.</p>
      )}
    </div>
  );
}
