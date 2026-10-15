import { stamp } from "./format";
import { pct } from "./format";
import { quantile } from "./slider";

/** The "made <ISO>" time inside a stored forecast stamp, or null when the stamp has none. */
export function madeAt(forecastStamp: string): string | null {
  const m = /made (\d{4}-\d{2}-\d{2}T[\d:.]+Z)/.exec(forecastStamp);
  return m?.[1] ?? null;
}

/** Where the number came from, as small chips: model, table kind, calibration, and when it was made. */
export function Chips(props: { model: string; stampText: string; tipUtc: string | null; extra?: string[] }) {
  const made = madeAt(props.stampText);
  const beforeTip = made !== null && props.tipUtc !== null && new Date(made).getTime() < new Date(props.tipUtc).getTime();
  return (
    <ul class="chips" aria-label="Where this number comes from">
      {props.model ? (
        <li class="chip" title={props.stampText}>
          {props.model}
        </li>
      ) : null}
      {props.extra?.map((e) => (
        <li key={e} class="chip">
          {e}
        </li>
      ))}
      {made !== null ? (
        <li class="chip num">
          made {stamp(made)}
          {beforeTip ? ", before tip" : ""}
        </li>
      ) : null}
    </ul>
  );
}

/** Over and under as one bar, so the two numbers read as a split, not two scores. */
export function ProbBar(props: { over: number; label: string }) {
  const o = Math.min(1, Math.max(0, props.over));
  return (
    <div class="probbar" role="img" aria-label={`${props.label}: over ${pct(o)}, under ${pct(1 - o)}`}>
      <i class="pb-over" style={{ width: `${(o * 100).toFixed(1)}%` }} />
      <i class="pb-under" style={{ width: `${((1 - o) * 100).toFixed(1)}%` }} />
    </div>
  );
}

/** The model's distribution over whole counts, one bar per count, the line marked between two bars. */
export function DistStrip(props: { pge: readonly number[]; line: number; model: number | null; unit: string }) {
  const { pge, line } = props;
  const lo = Math.max(0, quantile(pge, 0.01));
  const hi = Math.min(pge.length - 1, Math.max(lo + 4, quantile(pge, 0.99)));
  const ks: number[] = [];
  for (let k = lo; k <= hi; k++) ks.push(k);
  const mass = (k: number): number => (pge[k] ?? (k === 0 ? 1 : 0)) - (pge[k + 1] ?? 0);
  const max = Math.max(...ks.map(mass), 0.01);
  const w = 300;
  const h = 70;
  const bw = w / ks.length;
  const p10 = quantile(pge, 0.1);
  const p90 = quantile(pge, 0.9);
  const lineX = ((line + 0.5 - lo) / ks.length) * w;
  return (
    <svg
      class="diststrip"
      viewBox={`0 0 ${w.toString()} ${(h + 14).toString()}`}
      role="img"
      aria-label={`Model distribution of ${props.unit}, ${lo.toString()} to ${hi.toString()}. Middle 80 percent: ${p10.toString()} to ${p90.toString()}. Line ${line.toString()}.`}
    >
      <rect x={(p10 - lo) * bw} y="0" width={(p90 - p10 + 1) * bw} height={h} class="ds-band" />
      {ks.map((k, i) => {
        const bh = Math.max(1, (mass(k) / max) * (h - 6));
        return <rect key={k} x={i * bw + 0.8} y={h - bh} width={Math.max(1, bw - 1.6)} height={bh} class={k > line ? "ds-bar over" : "ds-bar"} />;
      })}
      {props.model !== null ? (
        <line x1={((props.model + 0.5 - lo) / ks.length) * w} x2={((props.model + 0.5 - lo) / ks.length) * w} y1="0" y2={h} class="ds-model" />
      ) : null}
      <line x1={lineX} x2={lineX} y1="0" y2={h + 4} class="ds-line" />
      <text x="0" y={h + 12} class="ds-tick">
        {lo}
      </text>
      <text x={w} y={h + 12} class="ds-tick" text-anchor="end">
        {hi}
      </text>
    </svg>
  );
}
