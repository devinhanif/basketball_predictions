import { api } from "../api";
import { View } from "../components";
import { useLoad } from "../useLoad";
import { longDate } from "../format";
import { STATUS_TEXT, cents, chancePct, monthProgress, signedUsd, usd, type Bankroll, type Move } from "../bankroll";

const TIERS = ["SAFE", "MEDIUM", "RISKY"] as const;

export function BankrollPage() {
  const [state, reload] = useLoad(() => api.bankroll(), []);
  return (
    <div class="stack">
      <div class="page-head">
        <h1>Bankroll</h1>
        <p class="muted">
          This is a plan, not an order. Nothing here places a bet or connects to an account; Devin places every order himself,
          after checking the live quote.
        </p>
      </div>
      <View state={state} reload={reload}>
        {(b: Bankroll) => <Body b={b} />}
      </View>
    </div>
  );
}

function Body({ b }: { b: Bankroll }) {
  const d = b.derived;
  const prog = monthProgress(b.account.cash, b.month_start.balance, d.target, d.stretch);
  const over = d.cash_planned > d.budget;
  return (
    <>
      <section class="panel" aria-label="Account">
        <h2>Account, {longDate(b.as_of)}</h2>
        <dl class="bk-strip num">
          <div><dt>Cash</dt><dd>{usd(b.account.cash)}</dd></div>
          <div><dt>Bonus credit</dt><dd>{usd(b.account.bonus)}</dd></div>
          <div><dt>Floor</dt><dd>{usd(d.floor)}</dd></div>
          <div><dt>Risk capital</dt><dd>{usd(d.risk_capital)}</dd></div>
          <div><dt>Today's budget</dt><dd>{usd(d.budget)}</dd></div>
        </dl>
        <p class="small muted">
          Floor = $25 x 1.01 per month since 2026-10-22 ({d.months.toString()} months so far). Risk capital = cash minus floor.
          Budget = 25% of risk capital.
        </p>
        {over ? (
          <p class="bk-warn small" role="status">
            The moves below ask for {usd(d.cash_planned)} of cash, more than today's budget of {usd(d.budget)}. Skip or shrink one
            before placing.
          </p>
        ) : null}
      </section>

      <section class="panel" aria-label="Month progress">
        <h2>Month progress</h2>
        <div class="bk-bar" role="img" aria-label={`Cash ${usd(b.account.cash)}, started ${usd(b.month_start.balance)}, target ${usd(d.target)}, stretch ${usd(d.stretch)}`}>
          <div class="bk-fill" style={{ width: `${(prog.fill * 100).toFixed(1)}%` }} />
          <div class="bk-mark" style={{ left: `${(prog.targetAt * 100).toFixed(1)}%` }} />
        </div>
        <div class="bk-scale num small">
          <span>Start {usd(b.month_start.balance)}</span>
          <span>+1% {usd(d.target)}</span>
          <span>+5% {usd(d.stretch)}</span>
        </div>
        <p class="small muted num">
          Since {longDate(b.month_start.date)}: {signedUsd(prog.delta)}. The bar runs from the month-start balance to the +5% stretch;
          the tick is the +1% target.
        </p>
      </section>

      <section class="stack tight" aria-label="Today's moves">
        <h2>Today's moves</h2>
        {b.moves.length === 0 ? (
          <div class="panel empty">
            <p><strong>No moves today. Keep your money.</strong></p>
            <p class="small muted">Nothing met its rule, and that is a result, not an error.</p>
          </div>
        ) : (
          b.moves.map((m) => <MoveCard key={m.id} m={m} />)
        )}
      </section>

      <section class="stack tight" aria-label="Open positions">
        <h2>Open positions</h2>
        {b.positions.length === 0 ? <p class="muted">No open positions.</p> : null}
        {b.positions.map((p) => (
          <article key={p.id} class="panel bk-card">
            <h3>{p.title}</h3>
            <ul class="bk-legs">
              {p.legs.map((l) => <li key={l}>{l}</li>)}
            </ul>
            <dl class="bk-facts num">
              <div><dt>Cost</dt><dd>{usd(p.cost)} ({p.source})</dd></div>
              <div><dt>Max payout</dt><dd>{usd(p.max_payout)}</dd></div>
              <div><dt>Fair value now</dt><dd>{usd(p.fair_now)}</dd></div>
              <div><dt>Sell if offer is</dt><dd>{usd(p.sell_if_offer_at_least)} or more</dd></div>
            </dl>
          </article>
        ))}
      </section>

      <section class="panel" aria-label="Daily scorecard">
        <h2>Daily scorecard</h2>
        {b.scorecard.length === 0 ? (
          <p class="muted">No settled days yet. The first row appears after the first day closes.</p>
        ) : (
          <div class="bk-scroll">
            <table class="st-table num">
              <thead>
                <tr>
                  <th scope="col">Date</th><th scope="col">End cash</th><th scope="col">P&amp;L</th>
                  {TIERS.map((t) => <th key={t} scope="col">{t}</th>)}
                  <th scope="col">Bonus to cash</th>
                </tr>
              </thead>
              <tbody>
                {b.scorecard.map((r) => (
                  <tr key={r.date}>
                    <th scope="row">{r.date}</th>
                    <td>{usd(r.end_cash)}</td>
                    <td>{signedUsd(r.pnl)}</td>
                    {TIERS.map((t) => <td key={t}>{signedUsd(r.pnl_by_tier[t] ?? 0)}</td>)}
                    <td>{usd(r.bonus_converted)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section class="panel" aria-label="Rules">
        <h2>The rules</h2>
        <ul class="bk-rules">
          <li><b>SAFE, 50% of the budget.</b> Bonus locks: 80 to 95 cent singles where our blend is at least the ask, as limit orders.</li>
          <li><b>MEDIUM, 30%.</b> Two- or three-leg combos, only when Kalshi's quote is at or below our joint chance.</li>
          <li><b>RISKY, 20%.</b> Correlated long shots, only when the quote is at or below 0.8 times our joint chance.</li>
          <li><b>Stop going live</b> if down more than $5 after 14 days.</li>
        </ul>
        <p class="small muted">{b.note} Chances are our model's estimates, not promises; most of these will lose.</p>
      </section>
    </>
  );
}

function MoveCard({ m }: { m: Move }) {
  return (
    <article class={`panel bk-card bk-${m.tier.toLowerCase()}`}>
      <div class="row wrap between">
        <span class="tag">{m.tier}</span>
        <span class={`bk-status s-${m.status}`}>{STATUS_TEXT[m.status]}</span>
      </div>
      <h3>{m.id}: {m.title}</h3>
      <dl class="bk-facts num">
        <div><dt>Stake</dt><dd>{usd(m.stake)} ({m.source})</dd></div>
        <div><dt>Buy only if quote is</dt><dd>{cents(m.limit)} or less</dd></div>
        <div><dt>Pays about</dt><dd>{usd(m.payout)}</dd></div>
        <div><dt>Our chance</dt><dd>about {chancePct(m.chance)}</dd></div>
      </dl>
      <p class="small muted">{m.reason}</p>
    </article>
  );
}
