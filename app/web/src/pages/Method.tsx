import { api } from "../api";
import { Explain, View } from "../components";
import { signed, stamp } from "../format";
import { useLoad } from "../useLoad";
import { STAT_LABEL } from "../types";
import type { Evidence, Meta } from "../types";
import { statWord } from "../words";

/** Who was closer to the truth, read off the interval and nothing else. */
function whoWon(ciLo: number, ciHi: number): { who: "book" | "model" | "tie"; text: string } {
  if (ciLo > 0) return { who: "book", text: "The sportsbook was closer" };
  if (ciHi < 0) return { who: "model", text: "Our model was closer" };
  return { who: "tie", text: "Too close to call" };
}

function EvidenceList({ ev }: { ev: Evidence }) {
  const g = ev.games_t60;
  const gw = whoWon(g.ci_lo, g.ci_hi);
  return (
    <>
      <ul class="scoreline">
        {ev.props_consensus_t60.map((r) => {
          const w = whoWon(r.ci_lo, r.ci_hi);
          return (
            <li key={r.stat}>
              <span class="sl-name">{STAT_LABEL[r.stat]} over/unders</span>
              <span class={`sl-who ${w.who}`}>{w.text}</span>
              <span class="sl-n num muted small">
                {r.n.toLocaleString()} lines, {r.games.toLocaleString()} games
              </span>
            </li>
          );
        })}
        <li>
          <span class="sl-name">Who wins the game</span>
          <span class={`sl-who ${gw.who}`}>{gw.text}</span>
          <span class="sl-n num muted small">{g.n.toLocaleString()} games</span>
        </li>
      </ul>
      <p class="small muted">
        {ev.seasonLabel} season, each forecast checked against the sportsbook's price from about one hour before tip. The words come from the
        95% range in the table below: if the range sits entirely on one side of zero, that side was closer. Otherwise it is too
        close to call.
      </p>
      <Explain>
        <p>
          We score every forecast by how surprised it was by what really happened. The score is called log loss, and lower is
          better. "Difference" is our score minus the sportsbook's, so a positive number means the sportsbook did better.
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Forecast</th>
                <th class="r">Lines</th>
                <th class="r">Games</th>
                <th class="r">Our score</th>
                <th class="r">Book's score</th>
                <th class="r">Difference</th>
                <th class="r">95% range</th>
              </tr>
            </thead>
            <tbody>
              {ev.props_consensus_t60.map((r) => (
                <tr key={r.stat}>
                  <td>{statWord(r.stat)}</td>
                  <td class="r num">{r.n.toLocaleString()}</td>
                  <td class="r num">{r.games.toLocaleString()}</td>
                  <td class="r num">{r.log_loss_model.toFixed(4)}</td>
                  <td class="r num">{r.log_loss_market.toFixed(4)}</td>
                  <td class="r num">{signed(r.delta)}</td>
                  <td class="r num">
                    {signed(r.ci_lo)} to {signed(r.ci_hi)}
                  </td>
                </tr>
              ))}
              <tr>
                <td>Game winners</td>
                <td class="r num">-</td>
                <td class="r num">{g.n.toLocaleString()}</td>
                <td class="r num">{g.log_loss_model.toFixed(4)}</td>
                <td class="r num">{g.log_loss_market.toFixed(4)}</td>
                <td class="r num">{signed(g.delta)}</td>
                <td class="r num">
                  {signed(g.ci_lo)} to {signed(g.ci_hi)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="small muted">
          {ev.ci_note}. Player lines: median across books with the margin removed. Games: {g.market}. Source:{" "}
          <span class="mono">{ev.source}</span>, rule <span class="mono">{ev.rule}</span>.
        </p>
      </Explain>
    </>
  );
}

function About({ meta }: { meta: Meta }) {
  return (
    <Explain label="Where the numbers come from">
      <ul class="plain small">
        <li>{meta.mode_note}</li>
        <li>
          Source data version <span class="mono">{meta.data_version}</span>, built {stamp(meta.built_at)}. Seasons up to{" "}
          {meta.max_season} (the 2024-25 season) only.
        </li>
        {Object.entries(meta.sources).map(([k, v]) => (
          <li key={k}>
            <span class="mono">{k}</span>: {v}
          </li>
        ))}
      </ul>
      <h3>What is missing for now</h3>
      <ul class="plain small">
        {meta.stubs.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </Explain>
  );
}

export function Method({ onStale }: { onStale: (s: boolean) => void }) {
  const [ev, reloadEv] = useLoad(() => api.evidence(), []);
  const [meta, reloadMeta] = useLoad(() => api.meta(), []);
  return (
    <div class="stack">
      <div class="page-head">
        <h1>How it works</h1>
      </div>

      <section class="panel verdict">
        <div class="kicker">The honest answer</div>
        <h2>Keep your money.</h2>
        <p class="lead">
          This is a way to watch how a basketball game and a forecast both work, with a pretend ledger to keep score. It is not
          a picks service. Until a live record shows our model can beat the books, the answer to "what should I bet?" is
          nothing.
        </p>
      </section>

      <section class="panel">
        <h2>Does the model beat the books? Not so far.</h2>
        <View state={ev} reload={reloadEv} onStale={onStale}>
          {(e) => <EvidenceList ev={e} />}
        </View>
      </section>

      <section class="panel">
        <h2>Where the forecasts come from</h2>
        <ul class="plain">
          <li>
            <strong>Who wins.</strong> A team rating that goes up when you win by a lot and down when you lose by a lot, adjusted
            for who is out. Every forecast you see was made before the game, by a model that had never seen that game or any
            later one.
          </li>
          <li>
            <strong>What players do.</strong> For each player we forecast a whole range of nights, not one number: the points,
            rebounds, assists and threes he might finish with. "Scores 25 or more about 6 times in 10" is read straight off that
            range. Past the edges of the range we say "nearly every time" or "very rarely" instead of making up a number.
          </li>
          <li>
            <strong>The books are the yardstick.</strong> We train on what happened, not on prices. We show the sportsbook's
            chance next to ours so you can see where we disagree.
          </li>
          <li>
            <strong>Over or under.</strong> Shows you only a line and a price, so your pick is your own read. It is locked in
            before tip-off. Then the real box score grades it.
          </li>
        </ul>
        <Explain label="Why some numbers have a range">
          <p>
            A forecast is a chance, not a promise. A pick that hits can still have been a bad bet, and a pick that misses can
            have been a good one. When we show a 95% range, it means: if we ran this check again on similar games, the
            answer would land inside it roughly 95 times in 100.
          </p>
        </Explain>
      </section>

      <section class="panel" id="responsible">
        <h2>Responsible gambling</h2>
        <ul class="plain">
          <li>You must be 21 or older. This is built for the United States.</li>
          <li>
            We do not take bets, sell picks, hold betting accounts or promise results. Everything here is a paper trade: no real
            money moves.
          </li>
          <li>A forecast is a chance, not a promise. Past results do not predict what comes next.</li>
          <li>
            If gambling is a problem for you or someone you know, call <strong>1-800-GAMBLER</strong>, a national helpline.
            Stopping is always a good option.
          </li>
        </ul>
      </section>

      <section class="panel flat">
        <View state={meta} reload={reloadMeta}>
          {(m) => <About meta={m} />}
        </View>
      </section>
    </div>
  );
}
