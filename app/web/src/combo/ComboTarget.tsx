// Framing 2, target probability: the combos of k over-legs with the most lift that still reach the chance you set.
import { useMemo, useState } from "preact/hooks";
import { Card } from "../ui";
import { OUT_OF_RANGE_NOTE, PlayLine, legWords, pc, type Ctx } from "./Bits";
import { candidateLegs, targetSearch } from "./engine";
import type { ComboGame, TargetResult } from "./types";

export function TargetTab(props: { ctx: Ctx; game: ComboGame }) {
  const { ctx, game } = props;
  const [target, setTarget] = useState(15);
  const [k, setK] = useState(2);
  const [floor, setFloor] = useState(false);
  const [res, setRes] = useState<{ r: TargetResult; k: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const droppedByRange = useMemo(() => {
    const mode = floor ? "main_line" : "p_band";
    return candidateLegs(game, 0.35, 0.75, mode, false).length - candidateLegs(game, 0.35, 0.75, mode, true).length;
  }, [game, floor]);
  const run = (): void => {
    setBusy(true);
    window.setTimeout(() => {
      const r = targetSearch(game, ctx.payload.shared, target / 100, k, 5, 40, floor ? "main_line" : "p_band", 0.35, 0.75, true);
      setRes({ r, k });
      setBusy(false);
    }, 20);
  };
  return (
    <div class="stack">
      <section class="panel" aria-labelledby="cb-tg-h">
        <h2 id="cb-tg-h">Aim for a chance</h2>
        <p class="small muted">
          Set the chance you want every leg to hit together and how many legs. We list the over-legs combos that reach it with the most lift: the legs that move
          together, with each leg between 35% and 75%. Legs above the tested range are never used. The best answer often has a leg near the 35% floor,
          because a low-chance leg is the one that gains most from moving with another.
        </p>
        <div class="cb-controls">
          <label class="field">
            <span>Target chance (%)</span>
            <input type="number" inputMode="numeric" min={1} max={60} step={1} value={target} onInput={(e) => { const v = Number(e.currentTarget.value); if (Number.isFinite(v)) setTarget(Math.min(60, Math.max(1, v))); }} />
          </label>
          <label class="field">
            <span>Legs</span>
            <select value={k} onChange={(e) => { setK(Number(e.currentTarget.value)); }}>
              <option value={2}>2</option>
              <option value={3}>3</option>
              <option value={4}>4</option>
            </select>
          </label>
          <label class="cb-check">
            <input type="checkbox" class="tap-check" checked={floor} onChange={(e) => { setFloor(e.currentTarget.checked); }} /> Only lines at or above the posted number
          </label>
          <button type="button" class="btn primary" onClick={run} disabled={busy}>
            {busy ? "Searching…" : "Find combos"}
          </button>
        </div>
        <p class="small muted">
          {OUT_OF_RANGE_NOTE}
          {droppedByRange > 0 ? ` ${droppedByRange.toString()} candidate legs in this game were left out for that reason.` : " No candidate leg in this game was left out for that reason."}
          {k >= 3 ? " Three or more legs are searched with a width-40 beam, so they are a model estimate, not an exhaustive answer." : ""}
        </p>
      </section>
      {res === null ? null : res.r.feasible ? (
        <p class="small" role="status">
          {res.r.combos.length.toString()} combos reach {pc(res.r.target, 0)} with {res.k.toString()} legs, most lift first.
        </p>
      ) : (
        <Card tone="empty">
          <p role="status">
            <strong>Nothing reaches {pc(res.r.target, 0)} with {res.k.toString()} legs here.</strong> The highest combined chance found is shown instead.
          </p>
        </Card>
      )}
      {res === null ? null : (
        <ol class="plain cb-rank">
          {res.r.combos.map((c) => (
            <li key={c.legs.map((l) => `${l.player_id.toString()}${l.stat}${l.line.toString()}`).join("|")}>
              <Card flat>
                <p class="cb-row-top">
                  <strong>{c.legs.map((l) => legWords(l, game)).join(" and ")}</strong>
                </p>
                <p class="cb-row-nums num">
                  <span>
                    together <b>{pc(c.p_joint)}</b>
                    {c.method === "qmc" && c.range_2se !== null ? <span class="muted small"> (± {pc(2 * c.se)}, 2 se)</span> : null}
                  </span>
                  <span>
                    unrelated <b>{pc(c.p_independent)}</b>
                  </span>
                  {c.lift !== null ? (
                    <span>
                      factor <b>{c.lift.toFixed(2)}</b>
                    </span>
                  ) : null}
                </p>
                <PlayLine legs={c.legs} game={game} pJoint={c.p_joint} />
                {c.label !== null ? <p class="small cb-estimate"><strong>{c.label}.</strong></p> : null}
              </Card>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
