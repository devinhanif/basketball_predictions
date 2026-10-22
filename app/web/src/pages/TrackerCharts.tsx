import { useState } from "preact/hooks";
import { comboSeries, legDeltas, payoffRows, type History } from "../optionsChart";
import { holdings, parseKalshiCsv, type Parsed } from "../csvImport";
import { describeLeg, money, type LegRow, type Parlay } from "../tracker";

const W = 340;
const H = 190;
const PAD = { l: 44, r: 10, t: 12, b: 26 };

function hhmm(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/* ------------------------------------------------------------------ value over time */
export function ValueChart(props: { rows: LegRow[]; shares: number | null; paid: number | null; hist: History | null }) {
  const { rows, shares, paid, hist } = props;
  if (shares === null) return <p class="small muted">Enter the number of contracts to draw the value chart.</p>;
  const tickers = rows.map((r) => r.leg.ticker);
  const s = comboSeries(tickers, hist?.series ?? {}, shares);
  if (s.points.length < 2) {
    const names = s.missing.map((i) => (rows[i] ? describeLeg(rows[i].leg) : "")).join("; ");
    return (
      <p class="small muted">
        {s.missing.length > 0
          ? `Not enough price history yet. A chart needs a Kalshi ticker on every leg and at least one price sample for each; missing: ${names}.`
          : "Not enough price history yet: one more sample and the line appears. Prices are sampled while this page is open."}
      </p>
    );
  }
  const t0 = (s.points[0] as { t: number }).t;
  const t1 = (s.points[s.points.length - 1] as { t: number }).t;
  const vals = s.points.map((p) => p.value);
  const lo = Math.min(0, ...vals, paid ?? 0);
  const hi = Math.max(...vals, paid ?? 0, shares * 0.02) * 1.08;
  const x = (t: number): number => PAD.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r);
  const y = (v: number): number => H - PAD.b - ((v - lo) / Math.max(1e-9, hi - lo)) * (H - PAD.t - PAD.b);
  const path = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const ticks = [lo, (lo + hi) / 2, hi];
  return (
    <figure class="opt-fig">
      <svg viewBox={`0 0 ${W.toString()} ${H.toString()}`} role="img" aria-label="Combo value over time">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} class="opt-grid" />
            <text x={PAD.l - 4} y={y(v) + 3} class="opt-axis" text-anchor="end">{money(v)}</text>
          </g>
        ))}
        {paid !== null ? (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(paid)} y2={y(paid)} class="opt-paid" />
            <text x={W - PAD.r} y={y(paid) - 3} class="opt-axis" text-anchor="end">paid {money(paid)}</text>
          </g>
        ) : null}
        <path d={path} class="opt-line" fill="none" />
        {s.marks.map((m) => (
          <g key={`${m.leg.toString()}-${m.kind}`}>
            <line x1={x(m.t)} x2={x(m.t)} y1={PAD.t} y2={H - PAD.b} class={`opt-mark ${m.kind}`} />
            <text x={x(m.t)} y={PAD.t + 9} class={`opt-marktext ${m.kind}`} text-anchor="middle">{(m.leg + 1).toString()}</text>
          </g>
        ))}
        <text x={PAD.l} y={H - 8} class="opt-axis">{hhmm(t0)}</text>
        <text x={W - PAD.r} y={H - 8} class="opt-axis" text-anchor="end">{hhmm(t1)}</text>
      </svg>
      <figcaption class="small muted">
        Value of the whole position at each moment: contracts x the product of each leg's mid price. Dashed line: what you
        paid. Numbered marks: a leg whose price went to 97c or more (hit) or 3c or less (dead) and stayed there; inferred
        from the price, not a settlement record. {s.marks.map((m) => `${(m.leg + 1).toString()} = ${rows[m.leg] ? describeLeg((rows[m.leg] as LegRow).leg) : ""} (${m.kind})`).join("; ")}
        {" "}Source: {hist?.capture ? "raw capture plus " : ""}samples of Kalshi public prices.
      </figcaption>
    </figure>
  );
}

/* ------------------------------------------------------------------ payoff and deltas */
export function PayoffDiagram(props: { rows: LegRow[]; shares: number | null; paid: number | null }) {
  const { rows, shares, paid } = props;
  const values = rows.map((r) => r.value);
  const skip = new Set(rows.flatMap((r, i) => (r.status.state === "done" || r.status.state === "dead" ? [i] : [])));
  if (shares === null) return null;
  const pr = payoffRows(shares, values, skip);
  if (pr.length === 0) return <p class="small muted">Every leg is settled: nothing left to move.</p>;
  const bw = W - PAD.l - 14;
  const max = shares;
  const rowH = 46;
  const h = pr.length * rowH + 28;
  const px = (v: number): number => PAD.l + (v / Math.max(1e-9, max)) * bw;
  const now = values.every((v) => v !== null) ? shares * values.reduce((a: number, v) => a * v, 1) : null;
  return (
    <figure class="opt-fig">
      <svg viewBox={`0 0 ${W.toString()} ${h.toString()}`} role="img" aria-label="Value if each remaining leg hits or misses">
        {pr.map((r, i) => {
          const y0 = 14 + i * rowH;
          const row = rows[r.leg] as LegRow;
          return (
            <g key={r.leg}>
              <text x={4} y={y0 + 4} class="opt-axis">{(r.leg + 1).toString()}</text>
              <rect x={PAD.l} y={y0 - 6} width={Math.max(1, px(r.ifHit) - PAD.l)} height={12} class="opt-bar hit" />
              <text x={px(r.ifHit) + 4} y={y0 + 4} class="opt-axis">hits {money(r.ifHit)}</text>
              <rect x={PAD.l} y={y0 + 10} width={2} height={12} class="opt-bar miss" />
              <text x={PAD.l + 6} y={y0 + 20} class="opt-axis">misses $0.00</text>
              <title>{describeLeg(row.leg)}</title>
            </g>
          );
        })}
        {paid !== null ? <line x1={px(paid)} x2={px(paid)} y1={4} y2={h - 16} class="opt-paid" /> : null}
        {now !== null ? <line x1={px(now)} x2={px(now)} y1={4} y2={h - 16} class="opt-now" /> : null}
        <text x={PAD.l} y={h - 4} class="opt-axis">$0</text>
        <text x={W - 14} y={h - 4} class="opt-axis" text-anchor="end">{money(max)} (all hit)</text>
      </svg>
      <figcaption class="small muted">
        Like an option payoff at expiry: for each open leg, what the whole position is worth if that leg hits (the other
        legs at their current prices) and if it misses (a parlay with one miss pays nothing).
        {paid !== null ? ` Dashed: paid ${money(paid)}.` : ""}
        {now !== null ? ` Solid: worth now ${money(now)}.` : ""} Legs: {pr.map((r) => `${(r.leg + 1).toString()} = ${describeLeg((rows[r.leg] as LegRow).leg)}`).join("; ")}.
      </figcaption>
    </figure>
  );
}

export function Deltas(props: { rows: LegRow[]; shares: number | null }) {
  const { rows, shares } = props;
  if (shares === null) return null;
  const skip = new Set(rows.flatMap((r, i) => (r.status.state === "done" || r.status.state === "dead" ? [i] : [])));
  const d = legDeltas(shares, rows.map((r) => r.value), skip);
  if (d.length === 0) return null;
  const top = Math.max(...d.map((x) => x.perCent), 1e-9);
  return (
    <div class="stack tight">
      <h3>Leg deltas</h3>
      <p class="small muted">
        How much the position's value moves if one leg's price moves 1 cent: contracts x the product of the other legs'
        prices, divided by 100. The top leg is the one the position depends on most.
      </p>
      <ul class="tk-leglist">
        {d.map((x) => {
          const r = rows[x.leg] as LegRow;
          return (
            <li key={x.leg} class="tk-leg tk-leg-col">
              <div class="row between">
                <b>{describeLeg(r.leg)}</b>
                <span class="num">{money(x.perCent)} per 1c</span>
              </div>
              <div class="opt-meter" aria-hidden="true">
                <span style={{ width: `${((x.perCent / top) * 100).toFixed(1)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function OptionsView(props: { parlay: Parlay; rows: LegRow[]; paid: number | null; hist: History | null }) {
  return (
    <section class="stack tight tk-opt" aria-label="Options view">
      <h3>Value over time</h3>
      <ValueChart rows={props.rows} shares={props.parlay.shares} paid={props.paid} hist={props.hist} />
      <h3>Payoff by leg</h3>
      <PayoffDiagram rows={props.rows} shares={props.parlay.shares} paid={props.paid} />
      <Deltas rows={props.rows} shares={props.parlay.shares} />
    </section>
  );
}

/* ------------------------------------------------------------------ CSV import */
export function CsvImport(props: {
  parlays: Parlay[];
  onApply: (parlayId: string, legId: string, ticker: string, entry: number | null) => void;
}) {
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const hs = parsed ? holdings(parsed.trades) : [];
  const legs = props.parlays.flatMap((p) => p.legs.map((l) => ({ p, l })));
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const read = (f: File | undefined): void => {
    if (!f) return;
    setName(f.name);
    setErr(null);
    const rd = new FileReader();
    rd.onload = () => {
      try {
        setParsed(parseKalshiCsv(typeof rd.result === "string" ? rd.result : ""));
      } catch {
        setParsed(null);
        setErr("This file could not be read as a CSV.");
      }
    };
    rd.onerror = () => { setErr("This file could not be read."); };
    rd.readAsText(f);
  };
  return (
    <details class="panel tk-csv">
      <summary>
        <b>Import your Kalshi trade history (CSV)</b>
      </summary>
      <div class="stack tight">
        <p class="small muted">
          Export your own trade history from Kalshi and choose the file here. It is read inside this page only: it is
          never uploaded to us or to anyone, and nothing is kept unless you apply a price to a leg (that is saved in this
          browser like the rest of your tracker). Nothing connects to your account.
        </p>
        <input type="file" accept=".csv,text/csv,text/plain" onChange={(e) => { read(e.currentTarget.files?.[0]); }} aria-label="Choose a CSV file" />
        {err ? <p class="live-warn" role="alert">{err}</p> : null}
        {parsed ? (
          <>
            <p class="small num">
              {name}: read {parsed.trades.length.toString()} trades{parsed.skipped > 0 ? `, skipped ${parsed.skipped.toString()} rows with no ticker` : ""}.
              Columns used: {Object.entries(parsed.columns).filter(([, v]) => v).map(([k, v]) => `${k} = "${v ?? ""}"`).join(", ") || "none"}.
              {parsed.ignoredColumns.length > 0 ? ` Ignored: ${parsed.ignoredColumns.join(", ")}.` : ""}
            </p>
            {parsed.notes.map((n) => <p key={n} class="small muted">{n}</p>)}
            {hs.length > 0 && legs.length === 0 ? <p class="small muted">Add a parlay with legs first, then apply a trade's price to one of them.</p> : null}
            <ul class="tk-leglist">
              {hs.slice(0, 60).map((h) => {
                const k = `${h.ticker}|${h.side}`;
                return (
                  <li key={k} class="tk-leg tk-leg-col">
                    <b class="tk-ticker">{h.ticker}</b>
                    <div class="small num">
                      {h.side || "side unknown"}: {h.contracts.toString()} contracts in {h.trades.toString()} buys
                      {h.avgPrice !== null ? `, average price ${(h.avgPrice * 100).toFixed(1)}c` : ""}
                    </div>
                    {legs.length > 0 ? (
                      <div class="row wrap">
                        <select
                          aria-label={`Leg for ${h.ticker}`}
                          value={chosen[k] ?? ""}
                          onChange={(e) => { const v = e.currentTarget.value; setChosen((c) => ({ ...c, [k]: v })); }}
                        >
                          <option value="">Apply to a leg...</option>
                          {legs.map(({ p, l }) => (
                            <option key={l.id} value={`${p.id}|${l.id}`}>{p.name}: {describeLeg(l)}</option>
                          ))}
                        </select>
                        <button
                          type="button"
                          class="btn"
                          disabled={!chosen[k]}
                          onClick={() => {
                            const [pid, lid] = (chosen[k] ?? "").split("|");
                            if (pid && lid) props.onApply(pid, lid, h.ticker, h.side === "no" || h.avgPrice === null ? null : Math.round(h.avgPrice * 1000) / 1000);
                          }}
                        >
                          Set ticker and price
                        </button>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {hs.length > 60 ? <p class="small muted">Showing the first 60 of {hs.length.toString()} tickers.</p> : null}
          </>
        ) : null}
      </div>
    </details>
  );
}
