// "If the game goes like this", Model tab: the calibrated chance as it stands, in the script, the square bar and the change.
// NO history counts appear here (display lock, Devin 2026-11-17): they are on the History tab, which shows no chance.
import { useMemo, useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { Badge, Card } from "../ui";
import { Bar } from "./Bar";
import { Face, HowToRead, OUT_OF_RANGE_NOTE, pc, teamName, type Ctx } from "./Bits";
import { statEntries } from "./engine";
import { STAT_NAME, lineMove, scriptSentence, scriptView, scriptWords, type ScriptRow, SCRIPT_TITLE } from "./script";
import type { ComboGame, ScriptName } from "./types";

type Extra = (row: ScriptRow) => ComponentChildren;

function Row(props: { ctx: Ctx; row: ScriptRow; kind: "more" | "less"; analyst: boolean; extra?: Extra }) {
  const { row, ctx, analyst } = props;
  const grid = statEntries(row.player).find((e) => e[0] === row.stat)?.[1];
  const move = grid ? lineMove(grid) : null;
  const d = row.change * 100;
  const sign = d >= 0 ? "+" : "−";
  return (
    <li class="cb-row">
      <Face name={row.player.name} team={teamName(ctx, row.player.team)} playerId={row.player.player_id} size={analyst ? "sm" : "lg"} />
      <div class="cb-who">
        <strong>{row.player.name}</strong>
        <span class="cb-legname">
          {row.side} {row.line.toString()} {STAT_NAME[row.stat] ?? row.stat}
        </span>
        {move !== null ? <span class="cb-move small muted">Posted {move}.</span> : null}
        {props.extra?.(row)}
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

function RowList(props: { ctx: Ctx; title: string; sub: string; rows: ScriptRow[]; kind: "more" | "less"; analyst: boolean; extra?: Extra }) {
  const [all, setAll] = useState(false);
  const cap = props.analyst ? 6 : 4; // Simple shows fewer rows
  const shown = all ? props.rows : props.rows.slice(0, cap);
  return (
    <Card title={props.title} class={`cb-list cb-${props.kind}`}>
            {props.rows.length === 0 ? (
        <p class="small">None.</p>
      ) : (
        <ul class="plain cb-rows">
          {shown.map((r) => (
            <Row key={`${r.player.player_id.toString()}-${r.stat}-${r.side}`} ctx={props.ctx} row={r} kind={props.kind} analyst={props.analyst} extra={props.extra} />
          ))}
        </ul>
      )}
      {props.rows.length > cap ? (
        <button type="button" class="btn small" onClick={() => { setAll(!all); }}>
          {all ? "Show fewer" : `Show all ${props.rows.length.toString()}`}
        </button>
      ) : null}
    </Card>
  );
}

/** `extra` is only passed when the merge-back switch is off (ComboScript.tsx); this file never touches a count itself. */
export function ModelView(props: { ctx: Ctx; game: ComboGame; script: ScriptName; analyst: boolean; extra?: Extra; merged?: ComponentChildren }) {
  const { ctx, game, script, analyst } = props;
  const shared = ctx.payload.shared;
  const view = useMemo(() => scriptView(game, shared, script), [game, shared, script]);
  const sentence = scriptSentence(script, view);
  const hasMain = game.players.some((p) => statEntries(p).some(([, g]) => g.main_line !== null));
  return (
    <div class="stack">
      {hasMain ? (
        <>
          <section class="panel cb-lead">
            {sentence !== null ? <p class="cb-sentence">{sentence}</p> : null}
            <p class="cb-key small muted">
              <i class="cb-key-now" aria-hidden="true" /> now · <i class="cb-key-script" aria-hidden="true" /> in the script · 30 to 70%
            </p>
          </section>
          <div class="cb-two">
            <RowList ctx={ctx} analyst={analyst} title="More likely if it happens" sub="" rows={view.more} kind="more" extra={props.extra} />
            <RowList ctx={ctx} analyst={analyst} title="Less likely if it happens" sub="" rows={view.less} kind="less" extra={props.extra} />
          </div>
        </>
      ) : (
        <p class="panel">This game has no posted lines in the file, so there is nothing to show for it.</p>
      )}
      <HowToRead ctx={ctx}>
        <p>
          <strong>{SCRIPT_TITLE[script]}</strong> means {scriptWords(script, game, shared)}. Tonight the model gives it <span class="num">{pc(view.prior)}</span> for this game, from its expected
          margin and total. <Badge tone="amber" icon="info" word="Exploratory" /> The effect of this script has not passed our own check as a calibrated number, so treat the changes as estimates.
        </p>
        <p>
          Lists are sorted by the size of the change; each group figure in the sentence is the average over that group's posted over-lines in this game. Bars run from 30% to 70%. The grey tick is the chance as it stands; the square is the chance if it happens. The band is green when the chance rises and red when it falls, and the
          signed change is always written beside it. Outside the scale a bar sits at the edge and says "off scale".
        </p>
        <p>
          Every line has two sides: when one side gets more likely the other gets less likely, so each line appears once in each list. {OUT_OF_RANGE_NOTE}
          {view.dropped > 0 ? ` ${view.dropped.toString()} of ${(view.dropped + view.offered).toString()} posted lines in this game were left out for that reason.` : " None of this game's posted lines were left out."}
        </p>
      </HowToRead>
    </div>
  );
}
