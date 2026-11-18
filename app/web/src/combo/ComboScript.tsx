// "If the game goes like this": pick a game script. Two tabs on the same legs (Devin, 2026-11-17, the strict reading of the
// LINE_CAL_LIVE display lock): "Model" (calibrated chances, the bar, the change; no counts) and "History" (this season's stored counts;
// no chance). SEPARATE_HISTORY (script.ts) turns this into one view again after the LINE_CAL_LIVE look.
import { useMemo, useState } from "preact/hooks";
import { pct } from "../format";
import { useMode } from "../prefs";
import { matchup, pc, type Ctx } from "./Bits";
import { HistoryLine, HistoryPanel, HistoryView, Trend } from "./ComboScriptHistory";
import { ModelView } from "./ComboScriptModel";
import { readHist, SCRIPT_REALISED, SCRIPT_SHORT, SCRIPT_NAME, SEPARATE_HISTORY, scriptView, scriptWords, statusWord, type ScriptRow } from "./script";
import { SCRIPTS, type ComboGame, type ScriptName } from "./types";
import { statEntries } from "./engine";

export type Sub = "model" | "history";

function Chooser(props: { ctx: Ctx; game: ComboGame; script: ScriptName; onPick: (s: ScriptName) => void; analyst: boolean; chance: boolean }) {
  const { ctx, game, script, analyst } = props;
  const shared = ctx.payload.shared;
  const prior = useMemo(() => Object.fromEntries(SCRIPTS.map((s) => [s, scriptView(game, shared, s).prior])) as Record<ScriptName, number>, [game, shared]);
  if (analyst) {
    return (
      <div class="cb-scroll">
        <table class="cb-script-table">
          <caption class="cb-cmd">
            combo --game {matchup(ctx, game)} --script {script} --lines posted --calibrated
          </caption>
          <thead>
            <tr>
              <th scope="col">script</th>
              <th scope="col">rule</th>
              {props.chance ? (
                <th scope="col" class="num">tonight</th>
              ) : (
                <>
                  <th scope="col" class="num">2023/24</th>
                  <th scope="col" class="num">season</th>
                </>
              )}
              <th scope="col">status</th>
            </tr>
          </thead>
          <tbody>
            {SCRIPTS.map((s) => {
              const h = shared.scripts.history?.[s];
              return (
                <tr key={s} class={script === s ? "on" : ""}>
                  <th scope="row">
                    <button type="button" class="cb-trow" aria-pressed={script === s} onClick={() => { props.onPick(s); }}>
                      {SCRIPT_SHORT[s]}
                    </button>
                  </th>
                  <td>{scriptWords(s, game, shared)}</td>
                  {props.chance ? (
                    <td class="num">{pc(prior[s])}</td>
                  ) : (
                    <>
                      <td class="num">
                        {pct(SCRIPT_REALISED[s].y2023, 0)}/{pct(SCRIPT_REALISED[s].y2024, 0)}
                      </td>
                      <td class="num">{h !== undefined ? `${h.n_occurred.toString()}/${h.n_games.toString()}` : "n/a"}</td>
                    </>
                  )}
                  <td>{statusWord(shared, s)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div class="cb-scripts" role="group" aria-label="Game script">
      {SCRIPTS.map((s) => (
        <button key={s} type="button" class="cb-script" aria-pressed={script === s} onClick={() => { props.onPick(s); }}>
          <span class="cb-script-title">{SCRIPT_NAME[s]}</span>
          <span class="cb-script-top">
            {props.chance ? <span class="small num">{pc(prior[s], 0)} tonight</span> : null}
            <span class="cb-tag">{statusWord(shared, s)}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

export function ScriptTab(props: { ctx: Ctx; game: ComboGame; sub: Sub }) {
  const { ctx, game, sub } = props;
  const analyst = useMode() === "analyst";
  const [script, setScript] = useState<ScriptName>("blowout");
  const showHistory = SEPARATE_HISTORY && sub === "history";
  // Merge-back (switch off): the counts return beside the chance on the model rows.
  const extra = SEPARATE_HISTORY
    ? undefined
    : (row: ScriptRow) => {
        const grid = statEntries(row.player).find((e) => e[0] === row.stat)?.[1];
        const h = grid ? readHist(grid.hist) : null;
        return (
          <>
            {h !== null ? <HistoryLine row={{ player: row.player, stat: row.stat, line: row.line, hist: h }} script={script} /> : null}
            <Trend game={game} playerId={row.player.player_id} stat={row.stat} line={row.line} side={row.side} />
          </>
        );
      };
  return (
    <div class="stack">
      <section class="panel cb-top" aria-label="Game script">
        <Chooser ctx={ctx} game={game} script={script} onPick={setScript} analyst={analyst} chance={!showHistory} />
      </section>
      {showHistory ? (
        <HistoryView ctx={ctx} game={game} script={script} analyst={analyst} />
      ) : (
        <>
          {SEPARATE_HISTORY ? null : <HistoryPanel ctx={ctx} game={game} script={script} />}
          <ModelView ctx={ctx} game={game} script={script} analyst={analyst} extra={extra} />
        </>
      )}
    </div>
  );
}
