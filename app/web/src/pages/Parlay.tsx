import { api } from "../api";
import { Badge, Card, PageHead } from "../ui";
import { Explain, View } from "../components";
import { longDate, stamp } from "../format";
import { engineSpeaksFor } from "../nights";
import { LatestTopParlays } from "../TopParlaysPanel";
import { useLoad } from "../useLoad";
import { useLens, useMode } from "../prefs";
import { MarketOnly } from "../Lens";
import { ParlayDiagnostics } from "../ParlayDiag";
import type { ParlayState } from "../types";
import { Door, DoorGroup } from "../Doors";
import { propsHref, topHref } from "../router";

/** Plain-language headline for the verdict. The engine's own label is kept in the details. */
export function verdictText(p: ParlayState): string {
  if (p.headline_verdict === "no_positive_ev_found") return "We found nothing worth betting. Keep your money.";
  return p.verdict_line;
}

export function whatWeChecked(p: ParlayState, compact: boolean): string {
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
  return `${gameText} We looked at ${read.toLocaleString()} open Kalshi markets (Kalshi is a prediction market). ${mapped.toString()} matched a game on the schedule, and ${priced.toString()} had a price on both sides${priced === 0 ? ", so there was nothing we could check" : ""}.`;
}

/** What the model's track record is, in words. `settled` in the data means graded. */
export function gateSentence(g: ParlayState["gate"]): string {
  if (g === null) return "We do not have a track record for this model to show yet.";
  if (g.gate_open) return "Our model has earned a track record, so it is allowed to vouch for a bet.";
  return `Our model has to earn trust first. It needs ${g.min_settled.toString()} graded picks over ${g.min_settled_dates.toString()} different days before we let it back a bet. So far: ${g.n_settled.toString()} picks over ${g.n_dates.toString()} days. Until then we just follow the sportsbooks.`;
}

export function Body({ p, compact }: { p: ParlayState; compact: boolean }) {
  const market = useLens() === "market";
  const analyst = useMode() === "analyst";
  const g = p.gate;
  const exact = (
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
      {market && p.fee_model !== null ? (
        <li>
          Fee schedule used: {p.fee_model.name} (formula {p.fee_model.formula}, coefficient {p.fee_model.coefficient}),
          verified: {p.fee_model.verified ? "yes" : "no"}. Source: {p.fee_model.source}.
        </li>
      ) : null}
      {market && p.price_basis ? (
        <li>
          How a ticket would be priced: {p.price_basis}. {p.historical_note}
        </li>
      ) : null}
      <li>
        Engine: {p.engine}. As of {stamp(p.as_of)}. Source: <span class="mono">{p.source}</span>.
      </li>
      <li>{p.disclaimer}</li>
    </ul>
  );
  return (
    <section class={`panel verdict${compact ? "" : " card-feature pb-verdict"}`} aria-labelledby="parlay-h">
      <div class="kicker">Parlay of the day</div>
      <h2 id="parlay-h">{p.title}</h2>
      <p class="lead">{verdictText(p)}</p>
      <p>{whatWeChecked(p, compact)}</p>
      <p class="small">
        {gateSentence(g)}{" "}
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
      {compact ? null : analyst ? (
        <div class="pb-foot pb-exact">
          <h3 class="small">The exact details</h3>
          {exact}
        </div>
      ) : (
        <Explain label="Show the exact details">{exact}</Explain>
      )}
    </section>
  );
}

/**
 * The engine's verdict is stored for one report date. On a page about another night (`date`), it is not
 * that night's verdict and is not shown as one (F05): a short note says which date it is for.
 */
export function ParlayCard({ compact, date }: { compact: boolean; date?: string | null }) {
  const [state, reload] = useLoad(() => api.parlay(), []);
  return (
    <View state={state} reload={reload}>
      {(p) =>
        engineSpeaksFor(date ?? null, p.report_date) ? (
          <Body p={p} compact={compact} />
        ) : (
          <section class="panel flat" aria-label="Parlay check">
            <p class="small">
              <strong>We did not run a parlay check for {longDate(date ?? p.report_date)}.</strong> The latest one is for{" "}
              {longDate(p.report_date)}: {verdictText(p)}
            </p>
            {compact ? null : (
              <p class="small">
                <a href="#/parlay">See that check</a>
              </p>
            )}
          </section>
        )
      }
    </View>
  );
}

export function ParlayPage(props: { date: string | null }) {
  const market = useLens() === "market";
  const analyst = useMode() === "analyst";
  return (
    <div class="stack pb-page">
      <PageHead
        kicker="Slip check"
        title="Parlay of the day"
        description={market ? "The engine's check of tonight's open parlay markets, after fees. Nothing worth betting is a normal answer." : <strong class="pb-sowhat">The engine's card belongs to the market side, so this lens gives the standing answer: keep your money.</strong>}
      />
      <DoorGroup label="Go to another screen">
        <Door to="props" href={propsHref()}>
          pick the lines for a slip
        </Door>
        <Door to="explore" href={topHref("combo")}>
          what changes if the game goes a certain way
        </Door>
        <Door to="teams" href="#/teams">
          tonight's games and win chances
        </Door>
      </DoorGroup>
      {/* The engine's card speaks of prices, fees and a book: Market lens only, like the ticket list below. */}
      {market ? (
        <ParlayCard compact={false} date={props.date} />
      ) : null}
      {/* The stored per-record joint chances name files and export fields: Analyst detail only. */}
      {market && analyst ? <ParlayDiagnostics date={props.date} /> : null}
      {market && !analyst ? <p class="small muted">The stored joint chances behind this card, record by record, are in Analyst detail (switch Detail at the top).</p> : null}
      {market ? (
        <LatestTopParlays date={props.date} />
      ) : (
        <MarketOnly level={2} name="The parlay card and ticket list" what="The engine's card and the night's ticket list are the market side." />
      )}
      <Card kicker="Reading the verdict" title="The three ways a day can go" class="pb-ways">
        <ul class="plain">
          <li>
            <Badge tone="better" icon="check" word="Trusted" />
            <strong>We have a bet we trust.</strong> The model has a track record and a ticket still looks good after the
            sportsbook's cut. We show it, with the record next to it, and it stays a paper ticket.
          </li>
          <li>
            <Badge tone="amber" icon="info" word="Unproven" />
            <strong>We have a guess, but it is unproven.</strong> The model has no track record yet. We may show its best
            idea, clearly marked as unproven. Keep your money.
          </li>
          <li>
            <Badge tone="none" icon="close" word="Keep your money" />
            <strong>Nothing today.</strong> No ticket was worth it, or there was nothing we could check. This is a normal
            day, not an error.
          </li>
        </ul>
      </Card>
    </div>
  );
}
