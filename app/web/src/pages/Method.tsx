import { api } from "../api";
import { View } from "../components";
import { signed, stamp } from "../format";
import { useLoad } from "../useLoad";
import { STAT_LABEL } from "../types";
import type { Evidence, Meta } from "../types";

function EvidenceTable({ ev }: { ev: Evidence }) {
  return (
    <>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Forecast</th>
              <th class="r">n</th>
              <th class="r">Games</th>
              <th class="r">Model</th>
              <th class="r">Market</th>
              <th class="r">Difference</th>
              <th class="r">95% CI</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {ev.props_consensus_t60.map((r) => (
              <tr key={r.stat}>
                <td>{STAT_LABEL[r.stat]} over/under</td>
                <td class="r num">{r.n.toLocaleString()}</td>
                <td class="r num">{r.games.toLocaleString()}</td>
                <td class="r num">{r.log_loss_model.toFixed(4)}</td>
                <td class="r num">{r.log_loss_market.toFixed(4)}</td>
                <td class="r num">{signed(r.delta)}</td>
                <td class="r num">
                  {signed(r.ci_lo)} to {signed(r.ci_hi)}
                </td>
                <td>{r.verdict.replace("_", " ")}</td>
              </tr>
            ))}
            <tr>
              <td>Game winners</td>
              <td class="r num">{ev.games_t60.n.toLocaleString()}</td>
              <td class="r num">{ev.games_t60.n.toLocaleString()}</td>
              <td class="r num">{ev.games_t60.log_loss_model.toFixed(4)}</td>
              <td class="r num">{ev.games_t60.log_loss_market.toFixed(4)}</td>
              <td class="r num">{signed(ev.games_t60.delta)}</td>
              <td class="r num">
                {signed(ev.games_t60.ci_lo)} to {signed(ev.games_t60.ci_hi)}
              </td>
              <td>{ev.games_t60.verdict.replace("_", " ")}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="small muted">
        Log loss, lower is better. Difference is model minus market, so a positive number means the market was better. {ev.ci_note}.
        Props: {ev.snapshot}, median de-vigged probability across books. Games: {ev.games_t60.market}. Season {ev.seasonLabel}.
        Source: <span class="mono">{ev.source}</span>, rule <span class="mono">{ev.rule}</span>.
      </p>
    </>
  );
}

function About({ meta }: { meta: Meta }) {
  return (
    <details>
      <summary>Where the numbers come from</summary>
      <ul class="plain small">
        <li>
          Mode: <strong>{meta.mode.replace("_", " ")}</strong>. {meta.mode_note}
        </li>
        <li>
          Source data version <span class="mono">{meta.data_version}</span>, fixtures built {stamp(meta.built_at)}, seasons up to {meta.max_season} (the 2024-25 season) only.
        </li>
        {Object.entries(meta.sources).map(([k, v]) => (
          <li key={k}>
            <span class="mono">{k}</span>: {v}
          </li>
        ))}
      </ul>
      <h3>Known stubs</h3>
      <ul class="plain small">
        {meta.stubs.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </details>
  );
}

export function Method({ onStale }: { onStale: (s: boolean) => void }) {
  const [ev, reloadEv] = useLoad(() => api.evidence(), []);
  const [meta, reloadMeta] = useLoad(() => api.meta(), []);
  return (
    <div class="stack">
      <h1>Method</h1>

      <section class="card verdict">
        <h2>The honest answer first</h2>
        <p class="big" style={{ margin: "8px 0" }}>
          Keep your money.
        </p>
        <p class="muted" style={{ margin: 0 }}>
          This is a way to see how a basketball game and a forecast both work, with a paper ledger to keep score. It is not a
          picks service, and until a live record proves skill, the right answer to "what should I bet?" is nothing.
        </p>
      </section>

      <section class="card">
        <h2>How it works</h2>
        <ul class="plain">
          <li>
            <strong>Win probability</strong> comes from a rating model (margin-of-victory Elo) adjusted for who is out. Each
            number you see was produced out-of-fold: the model had never seen that game or any later one.
          </li>
          <li>
            <strong>Player props</strong> are a distribution, not a point. We store 19 quantiles (5% to 95%) for points,
            rebounds, assists and threes. The chance of going over a line is read off that curve by interpolation, with a
            continuity correction because counts are whole numbers. It is an approximation; beyond the 5% and 95% points we
            say "95%+" or "under 5%" rather than invent a tail.
          </li>
          <li>
            <strong>The market is the benchmark.</strong> The book column is the posted price with the margin removed. We train
            on outcomes, not on prices, and we show both so you can see where they disagree.
          </li>
          <li>
            <strong>Swipe</strong> shows only a line and a price, so your answer is blind. It is stamped before tip and refused
            after. Then the real box score settles it.
          </li>
        </ul>
      </section>

      <section class="card" id="evidence">
        <h2>Does the model beat the market? Not so far.</h2>
        <View state={ev} reload={reloadEv} onStale={onStale}>
          {(e) => <EvidenceTable ev={e} />}
        </View>
      </section>

      <section class="card" id="responsible">
        <h2>Responsible gambling</h2>
        <ul class="plain">
          <li>You must be 21 or older, and this is built for the United States.</li>
          <li>
            We do not accept bets, route orders, hold betting credentials, sell picks or guarantee outcomes. Everything here is a
            paper trade.
          </li>
          <li>A forecast is a probability, not a promise. A pick that hits can still have been a bad bet, and the reverse.</li>
          <li>
            If gambling is a problem for you or someone you know, call <strong>1-800-GAMBLER</strong>, a national problem
            gambling helpline. Stopping is always a good option.
          </li>
          <li>Past results do not predict what comes next.</li>
        </ul>
      </section>

      <section class="card flat">
        <View state={meta} reload={reloadMeta}>
          {(m) => <About meta={m} />}
        </View>
      </section>
    </div>
  );
}
