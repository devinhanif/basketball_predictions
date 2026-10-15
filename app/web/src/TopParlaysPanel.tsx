import { api } from "./api";
import { Explain, View } from "./components";
import { american, bookName, lineText, pct, stamp } from "./format";
import { legHref } from "./router";
import { isLegStat } from "./legTypes";
import { evText, hasTickets, isKeepYourMoney, isStale, type TopParlays, type TpLeg, type TpTicket } from "./topParlays";
import { STAT_LABEL } from "./types";
import { useLoad } from "./useLoad";

function statName(s: string): string {
  const labels: Record<string, string | undefined> = STAT_LABEL;
  return labels[s] ?? s;
}

function LegRow({ leg }: { leg: TpLeg }) {
  const title = `${leg.player_name} ${leg.side} ${lineText(leg.line)} ${statName(leg.stat).toLowerCase()}`;
  return (
    <li class="tp-leg">
      <details>
        <summary>
          <span class="tp-leg-main">
            <strong>{leg.player_name}</strong> <span class="muted">{leg.team}</span>
            <span class="tp-leg-bet">
              {leg.side === "over" ? "Over" : "Under"} {lineText(leg.line)} {statName(leg.stat).toLowerCase()}
            </span>
          </span>
          <span class="tp-leg-price num">
            {bookName(leg.book)} {american(leg.price_american)}
          </span>
        </summary>
        <div class="tp-leg-why">
          <p class="small">{leg.reason}</p>
          <p class="small muted num">
            Model {pct(leg.p_raw)} raw, {pct(leg.p_cal)} calibrated. Model: {leg.model_source}. Price stamped{" "}
            {stamp(leg.price_at)}, before tip.
          </p>
          {isLegStat(leg.stat) ? (
            <a class="small" href={legHref(leg.game_id, leg.player_id, leg.stat, leg.side)} aria-label={`Open breakdown: ${title}`}>
              Open this leg's breakdown
            </a>
          ) : null}
        </div>
      </details>
    </li>
  );
}

function Ticket({ t, size }: { t: TpTicket; size: string }) {
  const [lo, hi] = t.p_joint_cal_80;
  return (
    <article class="tp-ticket" aria-label={`Most confident ${size}-leg, number ${t.rank.toString()}`}>
      <div class="tp-head">
        <span class="tp-rank">#{t.rank}</span>
        {t.same_game ? <span class="tag">same game</span> : null}
        <span class="muted small">paper ticket</span>
      </div>
      <ul class="plain tp-legs">
        {t.legs.map((l) => (
          <LegRow key={`${l.player_id.toString()}-${l.stat}-${l.side}`} leg={l} />
        ))}
      </ul>
      <dl class="tp-nums num">
        <div class="tp-main">
          <dt>Chance every leg hits (calibrated)</dt>
          <dd>
            <span class="big-num">{pct(t.p_joint_cal)}</span>
            <span class="small muted"> 80% range {pct(lo)} to {pct(hi)}</span>
          </dd>
          <dd class="small muted">Raw model: {pct(t.p_joint_raw)}</dd>
        </div>
        <div>
          <dt>Book's implied chance</dt>
          <dd>{pct(t.book_implied)}</dd>
        </div>
        <div>
          <dt>EV per $1</dt>
          <dd>
            {evText(t.ev_per_dollar)} <span class="small muted">low bound {evText(t.ev_low80)}</span>
          </dd>
        </div>
      </dl>
      <p class="small tp-why">{t.why}</p>
    </article>
  );
}

function List({ title, size, tickets }: { title: string; size: string; tickets: TpTicket[] }) {
  return (
    <section class="tp-list" aria-label={title}>
      <h3>{title}</h3>
      {tickets.length === 0 ? (
        <p class="small muted">No {size}-leg tickets for this night.</p>
      ) : (
        tickets.map((t) => <Ticket key={t.rank} t={t} size={size} />)
      )}
    </section>
  );
}

function Verdict({ d }: { d: TopParlays }) {
  const keep = isKeepYourMoney(d.verdict);
  return (
    <div class={`tp-verdict ${keep ? "keep" : "other"}`} role="status">
      <div class="kicker">Verdict</div>
      {keep ? (
        <p class="tp-verdict-text">Keep your money.</p>
      ) : (
        <p class="tp-verdict-text">
          Verdict: <span class="mono">{d.verdict}</span>
        </p>
      )}
      <p class="small">
        {keep
          ? "The model has not proven it beats the sportsbooks, so none of these is a bet. They are our most confident guesses, kept as paper tickets so we can score them."
          : "This label comes from the file. Every ticket here is still a paper ticket."}
      </p>
    </div>
  );
}

function Notes({ d }: { d: TopParlays }) {
  return (
    <div class="tp-notes small">
      {d.caveats.map((c) => (
        <p key={c}>
          <strong>Read this first.</strong> {c}
        </p>
      ))}
      <p>
        <strong>Calibration:</strong> {d.calibration.status}. Rule {d.calibration.rule} ({d.calibration.sha8}), fit
        through {d.calibration.fit_through}.
      </p>
    </div>
  );
}

function Body({ d, date }: { d: TopParlays; date: string }) {
  const stale = isStale(d.made_at, date);
  return (
    <section class="panel verdict tp" aria-labelledby="tp-h">
      <div class="kicker">Most confident parlays</div>
      <h2 id="tp-h">Our most confident tickets for the night</h2>
      <Verdict d={d} />
      {d.illustration ? (
        <p class="notice small" role="note">
          Illustration only: the players, lines, prices and chances in this file are synthetic.
        </p>
      ) : null}
      {stale ? (
        <p class="notice small" role="alert">
          These tickets are out of date. They were made {stamp(d.made_at)}, before this slate began. Do not read them as
          tonight's.
        </p>
      ) : null}
      <Notes d={d} />
      {hasTickets(d) ? (
        <>
          <p class="small muted">
            Ranked by {d.ranking}. Made {stamp(d.made_at)}.
          </p>
          <List title="Most confident 2-leg" size="2" tickets={d.sizes["2"]} />
          <List title="Most confident 3-leg" size="3" tickets={d.sizes["3"]} />
        </>
      ) : (
        <div class="tp-empty">
          <p class="lead">No tickets tonight.</p>
          <p>{d.empty_reason ?? "No tickets were made."}</p>
          <p class="small muted">This is a normal day, not an error.</p>
        </div>
      )}
      <Explain label="How to read a ticket">
        <p>
          <strong>Most confident</strong> means the highest chance, by our calibrated model, that every leg hits. It does
          not mean a good bet: a likely ticket can still pay less than it should.
        </p>
        <p>
          <strong>Calibrated</strong> means we shrank the raw model chance using how it did on past seasons. The 80% range
          is where we think the true chance sits, four times in five.
        </p>
        <p>
          <strong>EV per $1</strong> is what one dollar staked would earn on average, if our chance is right. The low
          bound is the cautious end. We only call a ticket worth it if the low bound is above zero.
        </p>
        <p>
          <strong>Paper only.</strong> Nothing here places a bet. Source: <span class="mono">top_parlays/1</span>, made{" "}
          {stamp(d.made_at)}.
        </p>
      </Explain>
    </section>
  );
}

function Message({ title, text }: { title: string; text: string }) {
  return (
    <section class="panel verdict tp" aria-labelledby="tp-h">
      <div class="kicker">Most confident parlays</div>
      <h2 id="tp-h">{title}</h2>
      <p class="tp-verdict-text">Keep your money.</p>
      <p>{text}</p>
    </section>
  );
}

export function TopParlaysPanel({ date }: { date: string }) {
  const [state, reload] = useLoad(() => api.topParlays(date), [date]);
  return (
    <View state={state} reload={reload}>
      {(r) =>
        r.kind === "ok" ? (
          <Body d={r.data} date={date} />
        ) : r.kind === "missing" ? (
          <Message
            title="No tickets file for this night"
            text="We have no most-confident tickets stored for this night yet. That is a normal gap, not an error. Check back after the pre-tip run."
          />
        ) : (
          <Message
            title="This night's tickets were refused"
            text={`The file did not pass our checks, so we are not showing it. ${r.detail}`}
          />
        )
      }
    </View>
  );
}

/** The newest slate date we hold, for pages that are not tied to a chosen night. */
export function LatestTopParlays() {
  const [list, reload] = useLoad(() => api.slates(), []);
  return (
    <View state={list} reload={reload}>
      {(slates) => {
        const date = slates[0]?.date;
        return date ? <TopParlaysPanel date={date} /> : <p>No nights are loaded.</p>;
      }}
    </View>
  );
}
