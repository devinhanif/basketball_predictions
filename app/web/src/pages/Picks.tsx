import { useMemo, useState } from "preact/hooks";
import { Explain, RecordBoard } from "../components";
import { american, lineText, longDate, pct } from "../format";
import { clearLedger, loadLedger, summarize, type Pick, type Settled } from "../ledger";
import { MIN_LEGS } from "../stats";
import { useSettled } from "../useSettled";
import { didText } from "../words";

function download(picks: Pick[]): void {
  const blob = new Blob([JSON.stringify(picks, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "my-paper-picks.json";
  a.click();
  URL.revokeObjectURL(url);
}

const BADGE = { win: "Hit", loss: "Miss", push: "Push" } as const;

function PickRow({ p, s, isOpen }: { p: Pick; s: Settled | undefined; isOpen: boolean }) {
  const skipped = p.side === "skip";
  const cls = skipped ? "skip" : s ? s.outcome : "open";
  const badge = skipped ? "Skip" : s ? BADGE[s.outcome] : "Open";
  let title: string;
  if (skipped) title = `${p.player}: you skipped this one`;
  else if (s) {
    title = `${p.player} ${didText(p.stat, s.row.actual)}`;
    if (s.outcome === "push") title += ", right on the line";
  } else title = isOpen ? `${p.player}: waiting on the game` : p.player;
  return (
    <li class={`pick ${cls}`}>
      <span class="badge">{badge}</span>
      <div class="pick-body">
        <div class="pick-title">{title}</div>
        <div class="pick-sub num">
          {skipped ? "" : `You took ${p.side} ${lineText(p.line)} (${american(p.side === "over" ? p.over_price : p.under_price)}). `}
          {p.team} vs {p.opponent}, {longDate(p.slate_date)}
        </div>
      </div>
    </li>
  );
}

export function Picks() {
  const [picks, setPicks] = useState<Pick[]>(loadLedger);
  const { results, settled, open } = useSettled(picks);
  const sum = useMemo(() => summarize(picks, settled), [picks, settled]);
  const [confirm, setConfirm] = useState(false);

  if (picks.length === 0) {
    return (
      <div class="stack">
        <div class="page-head">
          <h1>My picks</h1>
        </div>
        <div class="panel empty">
          <h2>No picks yet</h2>
          <p class="muted">Swipe a few cards. Each one gets graded against what really happened, and your record shows up here.</p>
          <a class="btn primary" href="#/swipe">
            Make some picks
          </a>
        </div>
      </div>
    );
  }

  return (
    <div class="stack">
      <div class="page-head">
        <h1>My picks</h1>
        <p class="muted">Pretend picks, saved only on this device. Each is graded against the real box score.</p>
      </div>

      {results.status === "error" ? (
        <div class="panel error" role="alert">
          <p style={{ margin: 0 }}>
            We could not fetch the results, so nothing is graded yet. <span class="mono">{results.message}</span>
          </p>
        </div>
      ) : null}

      <RecordBoard settled={settled} loading={results.status === "loading"} picks={sum.n_picks} />

      {sum.decided > 0 ? (
        <section class="panel" aria-labelledby="sum-h">
          <h2 id="sum-h">You vs the odds</h2>
          <p>
            Your picks won <b class="num">{pct(sum.wins / sum.decided)}</b> ({sum.wins} of {sum.decided}).{" "}
            {sum.mean_market !== null ? (
              <>
                The sportsbook's prices said the sides you took would win about <b class="num">{pct(sum.mean_market)}</b> of the
                time
              </>
            ) : null}
            {sum.mean_model !== null ? (
              <>
                {sum.mean_market !== null ? ", and our model said " : "Our model said "}
                <b class="num">{pct(sum.mean_model)}</b>
              </>
            ) : null}
            .
          </p>
          <p class="small muted num">
            {sum.enough && sum.hit_ci
              ? `The 95% range for your win rate is ${pct(sum.hit_ci[0])} to ${pct(sum.hit_ci[1])}. `
              : `Only ${sum.decided.toString()} graded, which is not enough to say whether this is skill or luck (we want at least ${MIN_LEGS.toString()}). `}
            {sum.pushes > 0 ? `${sum.pushes.toString()} landed exactly on the line and are left out. ` : ""}
            {sum.n_skips > 0 ? `${sum.n_skips.toString()} skipped.` : ""}
          </p>
          <Explain>
            <p>
              "Won" means you picked the right side of the line. The sportsbook's price turns into a chance once you remove
              its built-in margin: a side priced like a 52% shot should win about 52 times in 100. If your win rate sits
              near those numbers, you did about as well as the odds said you would.
            </p>
            <p>With a small number of picks, a hot or cold streak says very little. Differences here are mostly luck.</p>
          </Explain>
          <Explain label="Does the model's confidence hold up on your picks?">
            <p>
              We group your graded picks by how likely our model said your side was. If the model is honest, what happened
              should land close to what it predicted in every row. Rows with fewer than {MIN_LEGS} picks say "not enough to
              say".
            </p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Model said</th>
                    <th class="r">Picks</th>
                    <th class="r">Predicted</th>
                    <th class="r">Happened</th>
                    <th class="r">95% range</th>
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
          </Explain>
        </section>
      ) : null}

      <section class="panel" aria-labelledby="led-h">
        <div class="row between wrap">
          <h2 id="led-h">Every card you answered ({picks.length})</h2>
          <div class="row">
            <button type="button" class="btn" onClick={() => { download(picks); }}>
              Download
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
        <ul class="picks">
          {[...picks].reverse().map((p) => (
            <PickRow key={p.id} p={p} s={settled.find((x) => x.pick.id === p.id)} isOpen={open.some((o) => o.id === p.id)} />
          ))}
        </ul>
        <p class="small muted">
          Each pick is saved with the time you made it, how long you took, and a note that you never saw our forecast.
        </p>
      </section>
    </div>
  );
}
