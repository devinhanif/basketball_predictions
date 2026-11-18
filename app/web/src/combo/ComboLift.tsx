// Framing 1, game-script lift: which combos of main-line legs move together most, with the reason.
import { useEffect, useMemo, useState } from "preact/hooks";
import { Card } from "../ui";
import { OUT_OF_RANGE_NOTE, PlayLine, legWords, pc, type Ctx } from "./Bits";
import { liftRanking } from "./engine";
import { lineInRange } from "./script";
import type { ComboGame, RankedCombo } from "./types";

/** The game with every posted line that is outside the calibrated range switched off; the count says how many. */
export function calibratedOnly(game: ComboGame): { game: ComboGame; dropped: number } {
  let dropped = 0;
  const players = game.players.map((p) => ({
    ...p,
    stats: Object.fromEntries(
      Object.entries(p.stats).map(([k, g]) => {
        if (g.main_line !== null && !lineInRange(g, g.main_line)) {
          dropped += 1;
          return [k, { ...g, main_line: null }];
        }
        return [k, g];
      }),
    ),
  }));
  return { game: { ...game, players }, dropped };
}

export function LiftTab(props: { ctx: Ctx; game: ComboGame }) {
  const { ctx, game } = props;
  const cal = useMemo(() => calibratedOnly(game), [game]);
  const [rows, setRows] = useState<RankedCombo[] | null>(null);
  useEffect(() => {
    setRows(null);
    const t = window.setTimeout(() => {
      setRows(liftRanking(cal.game, ctx.payload.shared, 10));
    }, 20);
    return () => {
      window.clearTimeout(t);
    };
  }, [cal, ctx.payload.shared]);
  return (
    <div class="stack">
      <section class="panel">
        <h2>Which lines move together</h2>
        <p class="small muted">
          Pairs, and triples that hold one player's different stats, at the posted numbers (all overs or all unders), ranked by how much more often the legs hit
          together than if they were unrelated. Combos under 2% are left out. The same player's different stats lead the list; different players sit at about
          1.00. A larger factor does not make a combo likely or worth anything, only more tied together.
        </p>
        <p class="small muted">
          {OUT_OF_RANGE_NOTE}
          {cal.dropped > 0 ? ` ${cal.dropped.toString()} posted lines in this game were left out for that reason.` : " None of this game's posted lines were left out."}
        </p>
      </section>
      {rows === null ? (
        <p class="small muted" role="status">Working it out…</p>
      ) : rows.length === 0 ? (
        <p class="panel">No combo cleared the 2% floor for this game.</p>
      ) : (
        <ol class="plain cb-rank">
          {rows.map((r) => (
            <li key={r.legs.map((l) => `${l.player_id.toString()}${l.stat}`).join("|") + r.side}>
              <Card flat>
                <p class="cb-row-top">
                  <strong>{r.legs.map((l) => legWords(l, game)).join(" and ")}</strong>
                </p>
                <p class="cb-row-nums num">
                  <span>
                    together <b>{pc(r.p_joint)}</b>
                    {r.method === "qmc" && r.range_2se !== null ? <span class="muted small"> (± {pc(2 * r.se, 2)}, 2 se)</span> : null}
                  </span>
                  <span>
                    unrelated <b>{pc(r.p_independent)}</b>
                  </span>
                  <span>
                    factor <b>{r.lift.toFixed(2)}</b>
                  </span>
                </p>
                <PlayLine legs={r.legs} game={game} pJoint={r.p_joint} />
                <p class="small">{r.reason}</p>
                {r.label !== null ? <p class="small cb-estimate"><strong>{r.label}.</strong></p> : null}
                {r.above_tested_range ? <p class="small cb-warn"><strong>Above the tested range, likely too high.</strong></p> : null}
              </Card>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
