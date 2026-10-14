import { api } from "../api";
import { View } from "../components";
import { stamp } from "../format";
import { useLoad } from "../useLoad";
import type { ParlayState } from "../types";

function Body({ p, compact }: { p: ParlayState; compact: boolean }) {
  return (
    <section class="card verdict" aria-labelledby="parlay-h">
      <div class="row between wrap">
        <h2 id="parlay-h">Parlay of the day: {p.title.toLowerCase()}</h2>
        <span class="tag">state {p.state}</span>
      </div>
      <p class="big" style={{ margin: "8px 0" }}>
        Verdict: <span class="mono">{p.headline_verdict}</span>. {p.verdict_line}
      </p>
      <p class="muted">{p.reason}</p>
      {!compact ? (
        <>
          <div class="table-wrap">
            <table>
              <tbody>
                <tr>
                  <th scope="row">Model weight</th>
                  <td class="num">{p.model_weight.toFixed(2)}</td>
                  <td class="muted">{p.model_weight_note}</td>
                </tr>
                <tr>
                  <th scope="row">Gate</th>
                  <td class="num">
                    {p.gate.n_settled} of {p.gate.min_settled} settled
                  </td>
                  <td class="muted">
                    over {p.gate.n_dates} of {p.gate.min_settled_dates} slate dates; open = {String(p.gate.gate_open)}
                  </td>
                </tr>
                <tr>
                  <th scope="row">Record</th>
                  <td class="num">n = {p.gate.n_settled}</td>
                  <td class="muted">{p.gate.describe}</td>
                </tr>
                <tr>
                  <th scope="row">Fees</th>
                  <td class="num">{p.fee_model.name}</td>
                  <td class="muted">
                    {p.fee_model.formula}, coefficient {p.fee_model.coefficient}; verified: {String(p.fee_model.verified)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="small muted" style={{ marginTop: 12 }}>
            {p.price_basis}. {p.historical_note}
          </p>
          <p class="small muted">
            Engine: {p.engine}. As of {stamp(p.as_of)}. Source: <span class="mono">{p.source}</span>.
          </p>
          <p class="small muted">{p.disclaimer}</p>
        </>
      ) : (
        <p class="small" style={{ margin: 0 }}>
          Gate: {p.gate.n_settled} of {p.gate.min_settled} settled. The model defers to the market until it has a track
          record. Engine state as of {stamp(p.as_of)}. <a href="#/parlay">Why nothing today</a>
        </p>
      )}
    </section>
  );
}

export function ParlayCard({ compact }: { compact: boolean }) {
  const [state, reload] = useLoad(() => api.parlay(), []);
  return (
    <View state={state} reload={reload}>
      {(p) => <Body p={p} compact={compact} />}
    </View>
  );
}

export function ParlayPage() {
  return (
    <div class="stack">
      <h1>Parlay of the day</h1>
      <ParlayCard compact={false} />
      <div class="card flat">
        <h3>What the three states mean</h3>
        <ul class="plain small">
          <li>
            <strong>A.</strong> The gate is open and a ticket has positive expected value at the low end of its interval.
          </li>
          <li>
            <strong>B.</strong> The gate is closed. The best ticket may be shown, labelled hypothesis, unproven. Keep your
            money.
          </li>
          <li>
            <strong>C.</strong> No priced ticket, or no positive expected value. This is a normal day, not an error.
          </li>
        </ul>
      </div>
    </div>
  );
}
