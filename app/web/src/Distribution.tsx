// The stored distribution of a shown prop (distribution_v1): the 19-point quantile grid as a fan, the stored survival
// table at the line with its n, the chance to play and the stored conditional pieces exactly as the API returns them.
// Nothing is derived: a piece the export does not store says "not stored". The fan's geometry is layout only.
import { View } from "./components";
import { count, decimal, lineText, MISSING, percent } from "./format";
import { hasPriceWords, honestyApi, type Distribution, type Maybe } from "./honesty";
import type { LegStat } from "./legTypes";
import { useLens, useMode } from "./prefs";
import { termHref } from "./terms";
import { stamp } from "./time";
import { ChartFrame, Table, Tile } from "./ui";
import { useLoad } from "./useLoad";

const W = 360;
const H = 96;
const PAD = 18;
/** Symmetric pairs of grid indices drawn as nested bands (5-95, 15-85, 25-75, 35-65) and their shade. */
const BANDS: readonly [number, number, number][] = [
  [0, 18, 0.14],
  [2, 16, 0.24],
  [4, 14, 0.36],
  [6, 12, 0.5],
];

/** The smallest whole number above the line: "k or more" is the over side of a half line. */
export function overK(line: number): number {
  return Math.floor(line) + 1;
}

/** The stored survival count at k (k from 1), or null when k is outside the stored table. */
export function survivalAt(counts: readonly number[], k: number): number | null {
  if (k < 1) return null;
  return k - 1 < counts.length ? (counts[k - 1] ?? null) : null;
}

function Fan(props: { q: readonly number[]; line: number }) {
  const lo = Math.min(props.q[0] ?? 0, props.line);
  const hi = Math.max(props.q[18] ?? 1, props.line);
  const span = hi - lo || 1;
  const x = (v: number): number => PAD + ((v - lo) / span) * (W - 2 * PAD);
  const mid = props.q[9];
  return (
    <svg class="fan" viewBox={`0 0 ${W.toString()} ${H.toString()}`} role="img" aria-label="The stored range as nested bands; the table below holds the numbers">
      <line class="axis" x1={PAD} x2={W - PAD} y1={70} y2={70} />
      {BANDS.map(([a, b, o]) => {
        const qa = props.q[a];
        const qb = props.q[b];
        if (qa === undefined || qb === undefined) return null;
        return <rect key={a} class="band" x={x(qa)} y={20} width={Math.max(1, x(qb) - x(qa))} height={46} opacity={o} />;
      })}
      {mid !== undefined ? <line class="mid" x1={x(mid)} x2={x(mid)} y1={16} y2={70} /> : null}
      <line class="lineat" x1={x(props.line)} x2={x(props.line)} y1={8} y2={78} />
      <text x={x(props.line)} y={12} text-anchor="middle">line {lineText(props.line)}</text>
      <text x={PAD} y={90} text-anchor="start">{decimal(props.q[0], 1)}</text>
      {mid !== undefined ? <text x={x(mid)} y={90} text-anchor="middle">{decimal(mid, 1)}</text> : null}
      <text x={W - PAD} y={90} text-anchor="end">{decimal(props.q[18], 1)}</text>
    </svg>
  );
}

function Body(props: { d: Distribution; line: number; market: boolean; analyst: boolean }) {
  const { d, line, market, analyst } = props;
  const learnOk = (t: string | null | undefined): boolean => !!t && (market || !hasPriceWords(t));
  if (d.locked) {
    return (
      <>
        <p class="lead">{d.lock_text ?? "This game sits inside a locked test window, so its stored numbers are hidden."}</p>
        <p class="small muted">
          Model <span class="mono">{d.model_name ?? MISSING}</span> {d.version ? `(${d.version})` : ""}, made {d.made_at ? stamp(d.made_at) : MISSING}.
          {d.missing.length > 0 ? ` Why nothing is shown: ${d.missing.join("; ")}.` : ""}
        </p>
      </>
    );
  }
  const q = d.q_grid;
  const surv = d.p_ge_full;
  const k = overK(line);
  const c = surv ? survivalAt(surv.counts, k) : null;
  const tau = (i: number): string => percent(d.taus[i] ?? null);
  const c2 = d.conditional;
  const thresholds = c2?.p_ge_given_plays ? Object.keys(c2.p_ge_given_plays) : [];
  // The stored pieces behind the headline numbers: laid out in Analyst, one tap away in Simple.
  const stored = (
    <div class="dist-stored">
      <p class="small">
        Mean {decimal(d.mean, 2)}, spread (standard deviation) {decimal(d.std, 2)}, as stored.{" "}
        {d.support?.words ?? "Whether the values are whole numbers is not stored."}{" "}
        <a href={termHref("quantile-grid")}>Quantile grid</a>
      </p>
      {c2 ? (
        <>
          <p class="small">
            The forecast row itself was built with a chance to play of <strong class="num">{percent(c2.p_play_used, 1)}</strong>; it can differ from the one above,
            which comes from the availability model when one is stored. Mean if he plays {decimal(c2.mean_given_plays, 2)}; mean counting the chance he sits{" "}
            {decimal(c2.mean_unconditional, 2)}.
          </p>
          {thresholds.length > 0 ? (
            <Table
              caption="Stored chances at whole-number marks"
              rows={thresholds}
              rowKey={(t) => t}
              noun="marks"
              columns={[
                { key: "t", head: "Reaches", cell: (t) => `${t} or more`, num: true },
                { key: "g", head: "If he plays", cell: (t) => percent(c2.p_ge_given_plays?.[t] ?? null), num: true },
                { key: "u", head: "Counting the chance he sits", cell: (t) => percent(c2.p_ge_unconditional?.[t] ?? null), num: true },
              ]}
            />
          ) : null}
          {learnOk(c2.source) ? <p class="small muted">{c2.source}</p> : null}
        </>
      ) : (
        <p class="small">The pieces given he plays are not stored for this forecast.</p>
      )}
    </div>
  );
  return (
    <>
      {q !== null && q.length === 19 ? (
        <ChartFrame
          title="The stored forecast range"
          takeaway={`Half of the stored range sits between ${decimal(q[4], 1)} and ${decimal(q[14], 1)} (the ${tau(4)} and ${tau(14)} points); the middle (${tau(9)}) is ${decimal(q[9], 1)}.`}
          table={{ head: ["Point", "Value"], rows: d.taus.map((t, i) => [percent(t), decimal(q[i], 2)]), caption: "The 19 stored quantiles" }}
        >
          <Fan q={q} line={line} />
        </ChartFrame>
      ) : (
        <p>The stored quantile grid is not stored for this forecast.</p>
      )}
      <div class="dist-tiles">
        <Tile
          label={`stored samples reach ${k.toString()} or more (the over side of ${lineText(line)})`}
          value={surv ? `${c === null ? "none" : count(c)} of ${count(surv.n)}` : MISSING}
          n={surv ? `n = ${count(surv.n)} samples${analyst ? "; whole-number support" : ""}` : "The whole-number survival table is not stored for this forecast."}
        />
        <Tile
          label={analyst ? "chance to play shown on the site" : "chance he plays"}
          value={percent(d.p_play?.value ?? null, 1)}
          note={
            analyst ? (
              <>
                {d.p_play?.source ? `Source ${d.p_play.source}${d.p_play.detail ? `, ${d.p_play.detail}` : ""}. ` : ""}
                <a class="tap" href={termHref("p-play")}>p_play</a>
              </>
            ) : undefined
          }
        />
        <Tile label="projected minutes" value={decimal(d.proj_minutes, 1)} />
      </div>
      {analyst ? stored : (
        <details class="dist-more">
          <summary>More detail: mean, spread and the chances if he plays</summary>
          {stored}
        </details>
      )}
      <dl class="dist-facts">
        {market && d.kalshi_dnp_note ? (
          <div>
            <dt>If he is ruled out before tip-off</dt>
            <dd>
              {d.kalshi_dnp_note} <a href={termHref("dnp-rule")}>DNP rule</a>
            </dd>
          </div>
        ) : null}
        {d.missing.length > 0 ? (
          <div>
            <dt>Not stored</dt>
            <dd>{d.missing.join(", ")}.</dd>
          </div>
        ) : null}
        <div>
          <dt>Model</dt>
          <dd>
            {analyst ? <span class="mono">{d.model_name ?? MISSING}</span> : "Our props model"}
            {analyst && d.version ? <> (version <span class="mono">{d.version}</span>)</> : null}, made {d.made_at ? stamp(d.made_at) : MISSING}.
          </dd>
        </div>
      </dl>
    </>
  );
}

export function DistributionPanel(props: { gameId: string; playerId: number; stat: LegStat; line: number }) {
  const market = useLens() === "market";
  const mode = useMode();
  const [state, reload] = useLoad(() => honestyApi.distribution(props.gameId, props.playerId, props.stat), [props.gameId, props.playerId, props.stat]);
  return (
    <section class="panel" aria-labelledby="dist-h">
      <h2 id="dist-h">The whole forecast, as stored</h2>
      <View state={state} reload={reload} what="the stored distribution">
        {(m: Maybe<Distribution>) =>
          m.kind === "absent" ? (
            <p>
              <strong>Not in this snapshot.</strong> {m.why}
            </p>
          ) : (
            <Body d={m.data} line={props.line} market={market} analyst={mode === "analyst"} />
          )
        }
      </View>
    </section>
  );
}
