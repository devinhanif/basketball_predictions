import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "../api";
import { american, lineText, longDate, pct, stamp } from "../format";
import { clearLedger, loadLedger, settle, summarize, type Pick, type Settled } from "../ledger";
import { MIN_LEGS } from "../stats";
import { STAT_SHORT, type ResultRow } from "../types";

type ResultsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; rows: ResultRow[] };

function useResults(dates: string[]): ResultsState {
  const [state, setState] = useState<ResultsState>({ status: "loading" });
  const key = dates.join(",");
  useEffect(() => {
    let live = true;
    if (dates.length === 0) {
      setState({ status: "ready", rows: [] });
      return;
    }
    Promise.all(dates.map((d) => api.results(d))).then(
      (rs) => {
        if (live) setState({ status: "ready", rows: rs.flatMap((r) => r.data.rows) });
      },
      (e: unknown) => {
        if (live) setState({ status: "error", message: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      live = false;
    };
  }, [key]);
  return state;
}

function download(picks: Pick[]): void {
  const blob = new Blob([JSON.stringify(picks, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "my-paper-picks.json";
  a.click();
  URL.revokeObjectURL(url);
}

function Pill({ s }: { s: Settled }) {
  return <span class={`pill ${s.outcome}`}>{s.outcome}</span>;
}

export function Picks() {
  const [picks, setPicks] = useState<Pick[]>(loadLedger);
  const dates = useMemo(() => [...new Set(picks.map((p) => p.slate_date))].sort(), [picks]);
  const results = useResults(dates);
  const { settled, open } = useMemo(
    () => (results.status === "ready" ? settle(picks, results.rows) : { settled: [] as Settled[], open: [] as Pick[] }),
    [picks, results],
  );
  const sum = useMemo(() => summarize(picks, settled), [picks, settled]);
  const [confirm, setConfirm] = useState(false);

  if (picks.length === 0) {
    return (
      <div class="stack">
        <h1>My picks</h1>
        <div class="card empty">
          <h2>No picks yet</h2>
          <p class="muted">Swipe a few cards and they will appear here, settled against the real results.</p>
          <a class="btn primary" href="#/swipe">
            Go to Swipe
          </a>
        </div>
      </div>
    );
  }

  return (
    <div class="stack">
      <h1>My picks</h1>
      <p class="muted" style={{ margin: 0 }}>
        A paper ledger stored only on this device. <span class="tag result">result</span> means a real 2024-25 box-score
        outcome; <span class="tag forecast">forecast</span> means what the model said before tip.
      </p>

      {results.status === "error" ? (
        <div class="card error" role="alert">
          <p style={{ margin: 0 }}>
            Could not fetch results, so nothing is settled yet. <span class="mono">{results.message}</span>
          </p>
        </div>
      ) : null}

      <section class="card" aria-labelledby="sum-h">
        <h2 id="sum-h">How you did</h2>
        {sum.decided === 0 ? (
          <p class="muted">{results.status === "loading" ? "Settling..." : "Nothing settled yet."}</p>
        ) : (
          <>
            <p class="num" style={{ fontSize: "1.3rem", fontWeight: 700, margin: "8px 0 4px" }}>
              {sum.wins} of {sum.decided} correct{sum.hit_rate !== null ? ` (${pct(sum.hit_rate)})` : ""}
            </p>
            <p class="small muted num">
              n = {sum.decided} decided picks, {sum.pushes} pushes, {sum.n_skips} skips.{" "}
              {sum.enough && sum.hit_ci
                ? `95% interval for your hit rate: ${pct(sum.hit_ci[0])} to ${pct(sum.hit_ci[1])} (Wilson).`
                : `Not enough to say: an interval needs at least ${MIN_LEGS} decided picks.`}
            </p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Compare to</th>
                    <th class="r">Average probability of your side</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Your hit rate</td>
                    <td class="r num">{sum.hit_rate !== null ? pct(sum.hit_rate, 1) : "n/a"}</td>
                  </tr>
                  <tr>
                    <td>
                      <span class="tag market">book</span> de-vigged price
                    </td>
                    <td class="r num">{sum.mean_market !== null ? pct(sum.mean_market, 1) : "n/a"}</td>
                  </tr>
                  <tr>
                    <td>
                      <span class="tag forecast">model</span> at the time
                    </td>
                    <td class="r num">{sum.mean_model !== null ? pct(sum.mean_model, 1) : "n/a"}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="small muted">
              The book column is what a coin weighted by the price would hit; with a small n, differences here are mostly
              luck. Pushes are left out of every rate.
            </p>
          </>
        )}
      </section>

      <section class="card" aria-labelledby="cal-h">
        <h2 id="cal-h">Calibration of the model on the props you picked</h2>
        <p class="small muted">
          Group your decided picks by the model's probability for the side you took. If the model were calibrated, the
          observed rate would match the predicted rate in each row. Rows with fewer than {MIN_LEGS} picks say "not enough
          to say".
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model said</th>
                <th class="r">n</th>
                <th class="r">Predicted</th>
                <th class="r">Observed</th>
                <th class="r">95% interval</th>
              </tr>
            </thead>
            <tbody>
              {sum.bins.map((b) => (
                <tr key={b.label}>
                  <td>{b.label}</td>
                  <td class="r num">{b.n}</td>
                  <td class="r num">{b.predicted !== null ? pct(b.predicted) : "-"}</td>
                  <td class="r num">{b.observed !== null ? pct(b.observed) : "-"}</td>
                  <td class="r num">
                    {b.n === 0 ? "-" : b.enough && b.ci ? `${pct(b.ci[0])} to ${pct(b.ci[1])}` : "not enough to say"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section class="card" aria-labelledby="led-h">
        <div class="row between wrap">
          <h2 id="led-h">Ledger ({picks.length} cards answered)</h2>
          <div class="row">
            <button type="button" class="btn" onClick={() => { download(picks); }}>
              Download JSON
            </button>
            {confirm ? (
              <button
                type="button"
                class="btn primary"
                onClick={() => {
                  clearLedger();
                  setPicks([]);
                  setConfirm(false);
                }}
              >
                Yes, delete all
              </button>
            ) : (
              <button type="button" class="btn" onClick={() => { setConfirm(true); }}>
                Clear
              </button>
            )}
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Prop</th>
                <th>Pick</th>
                <th class="r">Actual</th>
                <th>Outcome</th>
                <th>Made</th>
              </tr>
            </thead>
            <tbody>
              {[...picks].reverse().map((p) => {
                const s = settled.find((x) => x.pick.id === p.id);
                return (
                  <tr key={p.id}>
                    <td>
                      {p.player} <span class="muted">{STAT_SHORT[p.stat]}</span> {lineText(p.line)}
                      <div class="muted small">
                        {longDate(p.slate_date)} · {p.team} vs {p.opponent}
                      </div>
                    </td>
                    <td>
                      {p.side === "skip" ? (
                        <span class="muted">skipped</span>
                      ) : (
                        <>
                          {p.side} <span class="muted num">{american(p.side === "over" ? p.over_price : p.under_price)}</span>
                        </>
                      )}
                    </td>
                    <td class="r num">{s ? s.row.actual : "-"}</td>
                    <td>{p.side === "skip" ? "-" : s ? <Pill s={s} /> : open.some((o) => o.id === p.id) ? "open" : "-"}</td>
                    <td class="small muted num">{stamp(p.made_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p class="small muted">
          Each row keeps made_at (real time), the replay clock used for the tip check, your response time and that the
          model was not shown (saw_model = false).
        </p>
      </section>
    </div>
  );
}
