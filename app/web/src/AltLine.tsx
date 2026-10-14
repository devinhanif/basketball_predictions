import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import { american, lineText, pctBounded } from "./format";
import type { ProbAtLine, PropView, Stat } from "./types";
import { STAT_LABEL } from "./types";
import { doesText, inTen } from "./words";

/** How far either side of the posted line the stepper goes. */
const REACH: Record<Stat, number> = { pts: 8, reb: 5, ast: 5, fg3m: 3 };

export function altRange(stat: Stat, main: number): { min: number; max: number } {
  const min = Math.max(0.5, Math.ceil((main - REACH[stat]) * 2) / 2);
  return { min, max: main + REACH[stat] };
}

function Side(props: { player: string; stat: Stat; line: number; side: "over" | "under"; p: number; extra: boolean }) {
  const { player, stat, line, side, p, extra } = props;
  return (
    <div class="alt-side">
      <div class="alt-say">
        {player} {doesText(stat, line, side)} {inTen(p, extra)}
      </div>
      <div class="alt-num">
        <div class="big-num num">{pctBounded(p, extra)}</div>
        <div class="alt-tag">{side}</div>
      </div>
    </div>
  );
}

/** Pick any line and see what the stored forecast says at it. Display only: nothing is saved or scored. */
export function AltLine(props: { gameId: string; playerId: number; player: string; prop: PropView }) {
  const { prop, gameId, playerId, player } = props;
  const main = prop.line;
  const { min, max } = altRange(prop.stat, main);
  const [line, setLine] = useState(main);
  const [res, setRes] = useState<ProbAtLine | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (line === main) return;
    let live = true;
    setFailed(false);
    const t = setTimeout(() => {
      api.propAtLine(gameId, playerId, prop.stat, line).then(
        (r) => {
          if (live) setRes(r.data);
        },
        () => {
          if (live) setFailed(true);
        },
      );
    }, 120);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [line, main, gameId, playerId, prop.stat]);

  const onMain = line === main;
  const ready = onMain || (res !== null && res.line === line);
  const p = onMain
    ? { over: prop.model_p_over, under: prop.model_p_under, push: prop.model_p_push, extra: prop.extrapolated }
    : res !== null && res.line === line
      ? { over: res.p_over, under: res.p_under, push: res.p_push, extra: res.extrapolated }
      : null;
  const step = (d: number): void => {
    setLine((l) => Math.min(max, Math.max(min, l + d)));
  };
  const id = `alt-${gameId}-${playerId.toString()}-${prop.stat}`;
  return (
    <details class="alt">
      <summary>Try your own line</summary>
      <div class="alt-body">
        <div class="stepper">
          <button type="button" class="btn" aria-label="Lower the line by half a point" disabled={line <= min} onClick={() => { step(-0.5); }}>
            -0.5
          </button>
          <div class="stepper-mid">
            <label class="label" for={id}>
              {STAT_LABEL[prop.stat]} line
            </label>
            <output class="line-out num" htmlFor={id}>
              {lineText(line)}
            </output>
          </div>
          <button type="button" class="btn" aria-label="Raise the line by half a point" disabled={line >= max} onClick={() => { step(0.5); }}>
            +0.5
          </button>
        </div>
        <input
          id={id}
          class="slider"
          type="range"
          min={min}
          max={max}
          step={0.5}
          value={line}
          onInput={(e) => {
            setLine(Number(e.currentTarget.value));
          }}
        />
        <div class="muted small num alt-ends">
          <span>{lineText(min)}</span>
          <button type="button" class="linklike" disabled={onMain} onClick={() => { setLine(main); }}>
            Back to the {prop.line_source === "posted" ? "posted" : "starting"} line ({lineText(main)})
          </button>
          <span>{lineText(max)}</span>
        </div>
        {failed ? <p role="alert">Could not work that line out. Try again.</p> : null}
        {p ? (
          <div class="alt-result" aria-live="polite">
            <Side player={player} stat={prop.stat} line={line} side="over" p={p.over} extra={p.extra} />
            <Side player={player} stat={prop.stat} line={line} side="under" p={p.under} extra={p.extra} />
            {Number.isInteger(line) && p.push > 0.005 ? (
              <p class="small muted num">Exactly {lineText(line)} is a push: {Math.round(p.push * 100)}%.</p>
            ) : null}
            <p class="small num">
              {onMain && ready && prop.over_price !== null && prop.under_price !== null
                ? `Posted prices: over ${american(prop.over_price)}, under ${american(prop.under_price)}.`
                : "No price for this line."}
            </p>
          </div>
        ) : failed ? null : (
          <p class="muted small">Working it out...</p>
        )}
        <p class="small muted">
          Our forecasts are checked at the main lines, not at other lines, so far-off lines are rougher. This is for
          looking only: nothing here is saved or scored.
        </p>
      </div>
    </details>
  );
}
