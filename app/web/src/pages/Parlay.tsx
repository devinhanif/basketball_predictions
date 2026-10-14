import { api } from "../api";
import { Explain, View } from "../components";
import { stamp } from "../format";
import { useLoad } from "../useLoad";
import type { ParlayState } from "../types";

/** Plain-language headline for the verdict. The engine's own label is kept in the details. */
function verdictText(p: ParlayState): string {
  if (p.headline_verdict === "no_positive_ev_found") return "We found nothing worth betting. Keep your money.";
  return p.verdict_line;
}

function whatWeChecked(p: ParlayState, compact: boolean): string {
  const c = p.counts;
  const games = c["n_games"];
  const read = c["n_markets_read"];
  const mapped = c["n_mapped"];
  const priced = c["n_contracts"];
  if (games === undefined || read === undefined || mapped === undefined || priced === undefined) return p.reason;
  if (compact) {
    return `We looked at ${read.toLocaleString()} open markets. ${mapped.toString()} matched a game we have a forecast for.`;
  }
  const gameText =
    games === 0 ? "We had no game forecasts stored for this date." : `We had forecasts for ${games.toString()} games.`;
  return `${gameText} We looked at ${read.toLocaleString()} open Kalshi markets (Kalshi is a prediction market). ${mapped.toString()} matched a game on the schedule, and ${priced.toString()} had a price on both sides, so there was nothing we could check.`;
}

function Body({ p, compact }: { p: ParlayState; compact: boolean }) {
  const g = p.gate;
  return (
    <section class="panel verdict" aria-labelledby="parlay-h">
      <div class="kicker">Parlay of the day</div>
      <h2 id="parlay-h">{p.title}</h2>
      <p class="lead">{verdictText(p)}</p>
      <p>{whatWeChecked(p, compact)}</p>
      <p class="small">
        {g === null
          ? "The track record behind this answer is not part of the data we have."
          : g.gate_open
            ? "Our model has earned a track record, so it is allowed to vouch for a bet."
            : `Our model has to earn trust first. It needs ${g.min_settled.toString()} graded picks over ${g.min_settled_dates.toString()} different days before we let it back a bet. So far: ${g.n_settled.toString()} picks over ${g.n_dates.toString()} days. Until then we just follow the sportsbooks.`}{" "}
        {compact ? <a href="#/parlay">The full story</a> : null}
      </p>
      <Explain>
        <p>
          <strong>Parlay.</strong> One ticket with several bets stacked together. Every one has to hit or the whole thing
          loses. That is why the payout looks big and why parlays lose often.
        </p>
        <p>
          <strong>Worth betting.</strong> We only call a bet worth it if, on average, you would come out ahead after the
          sportsbook's cut and the fees. We use the cautious end of our own estimate, not the hopeful end.
        </p>
        <p>
          <strong>Paper only.</strong> Nothing here places a bet. Any ticket we show is pretend, so we can keep score
          honestly.
        </p>
      </Explain>
      {!compact ? (
        <Explain label="Show the exact details">
          <ul class="plain small">
            <li>
              The engine's own label: <span class="mono">{p.headline_verdict}</span>. Its note: {p.reason}
            </li>
            {p.model_weight !== null ? (
              <li>
                Model weight: <span class="num">{p.model_weight.toFixed(2)}</span>. {p.model_weight_note}
              </li>
            ) : null}
            {g !== null ? (
              <li>
                Track record: {g.describe}; gate open: {g.gate_open ? "yes" : "no"}.
              </li>
            ) : null}
            {p.fee_model !== null ? (
              <li>
                Fee schedule used: {p.fee_model.name} (formula {p.fee_model.formula}, coefficient {p.fee_model.coefficient}),
                verified: {p.fee_model.verified ? "yes" : "no"}. Source: {p.fee_model.source}.
              </li>
            ) : null}
            {p.price_basis ? (
              <li>
                How a ticket would be priced: {p.price_basis}. {p.historical_note}
              </li>
            ) : null}
            <li>
              Engine: {p.engine}. As of {stamp(p.as_of)}. Source: <span class="mono">{p.source}</span>.
            </li>
            <li>{p.disclaimer}</li>
          </ul>
        </Explain>
      ) : null}
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
      <section class="panel flat">
        <h3>The three ways a day can go</h3>
        <ul class="plain">
          <li>
            <strong>We have a bet we trust.</strong> The model has a track record and a ticket still looks good after the
            sportsbook's cut. We show it, with the record next to it, and it stays a paper ticket.
          </li>
          <li>
            <strong>We have a guess, but it is unproven.</strong> The model has no track record yet. We may show its best
            idea, clearly marked as unproven. Keep your money.
          </li>
          <li>
            <strong>Nothing today.</strong> No ticket was worth it, or there was nothing we could price. This is a normal
            day, not an error.
          </li>
        </ul>
      </section>
    </div>
  );
}
