import { api } from "./api";
import { Explain, View } from "./components";
import { american, bookName, lineText, longDate, pct, signed, stamp } from "./format";
import { legHref } from "./router";
import { isLegStat } from "./legTypes";
import {
  etDate,
  evText,
  hasTickets,
  isKeepYourMoney,
  isStale,
  madeAfterTip,
  type TopParlays,
  type TpLeg,
  type TpTicket,
} from "./topParlays";
import { STAT_LABEL } from "./types";
import { useErrorBoundary } from "preact/hooks";
import { useLoad } from "./useLoad";

function statName(s: string): string {
  const labels: Record<string, string | undefined> = STAT_LABEL;
  return labels[s] ?? s;
}

function LegRow({ leg, illustration }: { leg: TpLeg; illustration: boolean }) {
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
            {leg.tail_flag === true ? <span class="tag tail-flag">65%+ leg: ran high in tests</span> : null}
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
          {!illustration && isLegStat(leg.stat) ? (
            <a class="small" href={legHref(leg.game_id, leg.player_id, leg.stat, leg.side)} aria-label={`Open breakdown: ${title}`}>
              Open this leg's breakdown
            </a>
          ) : null}
        </div>
      </details>
    </li>
  );
}

function Ticket({ t, size, illustration }: { t: TpTicket; size: string; illustration: boolean }) {
  const [lo, hi] = t.p_joint_cal_80;
  return (
    <article class="tp-ticket" aria-label={`Most confident ${size}-leg, number ${t.rank.toString()}`}>
      <div class="tp-head">
        <span class="tp-rank">#{t.rank}</span>
        {illustration ? <span class="tag illus">Illustration</span> : null}
        {t.same_game ? <span class="tag">same game</span> : null}
        <span class="muted small">paper ticket</span>
      </div>
      <ul class="plain tp-legs">
        {t.legs.map((l) => (
          <LegRow key={`${l.player_id.toString()}-${l.stat}-${l.side}`} leg={l} illustration={illustration} />
        ))}
      </ul>
      <dl class="tp-nums num">
        <div class="tp-main">
          <dt>Chance every leg hits (legs calibrated; joint not yet tested)</dt>
          <dd>
            <span class="big-num">{pct(t.p_joint_cal)}</span>
            <span class="small muted"> 80% range {pct(lo)} to {pct(hi)}</span>
          </dd>
          <dd class="small muted">Raw model: {pct(t.p_joint_raw)}</dd>
        </div>
        <div>
          <dt>Book's implied chance (not a tradable price)</dt>
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
      {t.tail_note ? <p class="small tp-tail-note">{t.tail_note}</p> : null}
    </article>
  );
}

function List(props: { title: string; size: string; tickets: TpTicket[]; illustration: boolean }) {
  const { title, size, tickets, illustration } = props;
  return (
    <section class="tp-list" aria-label={title}>
      <h3>{title}</h3>
      {tickets.length === 0 ? (
        <p class="small muted">No {size}-leg tickets for this night.</p>
      ) : (
        tickets.map((t, i) => <Ticket key={`${i.toString()}-${t.rank.toString()}`} t={t} size={size} illustration={illustration} />)
      )}
    </section>
  );
}

function Verdict({ d }: { d: TopParlays }) {
  const claimsEdge = !isKeepYourMoney(d.verdict); // the API only lets this through with an open gate
  return (
    <div class={`tp-verdict ${claimsEdge ? "other" : "keep"}`} role="status">
      <div class="kicker">Verdict</div>
      {claimsEdge ? (
        <p class="tp-verdict-text">The gate is open. Paper only.</p>
      ) : (
        <p class="tp-verdict-text">Keep your money.</p>
      )}
      <p class="small">
        {claimsEdge
          ? "At least one ticket clears the bar at its low bound, and the model has a live record that passed its gate. Every ticket here is still a paper ticket."
          : "The model has not proven it beats the sportsbooks, so none of these is a bet. They are our most confident guesses, kept as paper tickets so we can score them."}
      </p>
    </div>
  );
}

function GateLine({ g }: { g: TopParlays["gate"] }) {
  return (
    <p class="small tp-gate num">
      {g.open
        ? `Gate open: ${g.settled.toString()} settled over ${g.slate_dates.toString()} slate dates, skill versus the market ${g.skill_vs_market === null ? "not stated" : signed(g.skill_vs_market)}.`
        : `The model defers to the market until it has a track record (needs >= ${g.settled_min.toString()} settled, >= ${g.dates_min.toString()} slate dates, positive skill; now n = ${g.settled.toString()} over ${g.slate_dates.toString()} dates${g.skill_vs_market === null ? "" : `, skill versus the market ${signed(g.skill_vs_market)}`}).`}
    </p>
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

function Body({ d, checkToday }: { d: TopParlays; checkToday: boolean }) {
  const stale = isStale(d.made_at, d.slate_date);
  const lateFile = madeAfterTip(d);
  const notToday = checkToday && d.slate_date !== etDate(new Date());
  return (
    <section class="panel verdict tp" aria-labelledby="tp-h">
      <div class="kicker">{d.illustration ? "ILLUSTRATION" : "Most confident parlays"}</div>
      <h2 id="tp-h">
        {d.illustration
          ? "ILLUSTRATION: synthetic, not a real night"
          : `Our most confident tickets for ${longDate(d.slate_date)}`}
      </h2>
      <Verdict d={d} />
      <GateLine g={d.gate} />
      {d.illustration ? (
        <p class="notice small" role="note">
          Illustration only: the players, lines, prices and chances in this file are synthetic. It stands for the shape of a
          real night and says nothing about one.
        </p>
      ) : null}
      {stale ? (
        <p class="notice small" role="alert">
          These tickets are out of date. They were made {stamp(d.made_at)}, before this slate's day began. Do not read them
          as tonight's.
        </p>
      ) : null}
      {lateFile ? (
        <p class="notice small" role="alert">
          Made after the first tip ({stamp(d.made_at)}); not a forecast.
        </p>
      ) : null}
      {notToday ? (
        <p class="notice small" role="note">
          This is the night of {longDate(d.slate_date)}, not today (Eastern time).
        </p>
      ) : null}
      <Notes d={d} />
      {hasTickets(d) ? (
        <>
          {d.gate.open ? null : <span class="tag hypo">Hypothesis, unproven</span>}
          <p class="small muted">
            Ranked by {d.ranking}. Made {stamp(d.made_at)}.
          </p>
          <List title="Most confident 2-leg" size="2" tickets={d.sizes["2"]} illustration={d.illustration} />
          <List title="Most confident 3-leg" size="3" tickets={d.sizes["3"]} illustration={d.illustration} />
        </>
      ) : (
        <div class="tp-empty">
          <p class="lead">No tickets tonight.</p>
          <p>Reason: {d.empty_reason ?? "none given"}.</p>
          <p class="small muted">This is a normal day, not an error.</p>
        </div>
      )}
      <Explain label="How to read a ticket">
        <p>
          <strong>Most confident</strong> means the highest chance, by our model, that every leg hits. It does not mean a
          good bet: a likely ticket can still pay less than it should.
        </p>
        <p>
          <strong>Legs calibrated, joint not yet tested.</strong> Each leg's chance was shrunk using how the model did on
          past seasons. The combined chance, and its 80% range, are built from those legs and have not been checked
          themselves. The 80% range is the model's own spread; how often it holds has not been checked yet.
        </p>
        <p>
          <strong>Book's implied chance</strong> is the product of the legs' prices. It is not a price anyone offers for the
          whole ticket.
        </p>
        <p>
          <strong>EV per $1</strong> is what one dollar staked would earn on average, if our chance is right. The low
          bound is the cautious end. We only call a ticket worth it if the low bound is above zero, and only after the
          model's live record passes its gate.
        </p>
        <p>
          <strong>Paper only.</strong> Nothing here places a bet. Source: <span class="mono">top_parlays/2</span>, made{" "}
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

/** The panel may fail on its own; the page around it must never go blank. */
export function TopParlaysPanel(props: { date: string; checkToday?: boolean }) {
  const boundary = useErrorBoundary();
  const failed: unknown = boundary[0];
  if (failed !== undefined) {
    return (
      <Message
        title="This night's tickets could not be shown"
        text="Something in the tickets file broke the display, so we are not showing it."
      />
    );
  }
  return <PanelInner date={props.date} checkToday={props.checkToday ?? false} />;
}

function PanelInner({ date, checkToday }: { date: string; checkToday: boolean }) {
  const [state, reload] = useLoad(() => api.topParlays(date), [date]);
  const saved = state.status === "ready" && state.fromCache;
  return (
    <>
      {saved ? (
        <p class="notice small" role="status">
          This is a saved copy from earlier, not a fresh load.
        </p>
      ) : null}
      <View state={state} reload={reload}>
        {(r) =>
          r.kind === "ok" ? (
            <Body d={r.data} checkToday={checkToday} />
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
    </>
  );
}

/** The chosen night (hash `?date=`), else the newest slate date we hold. */
export function LatestTopParlays({ date }: { date: string | null }) {
  const [list, reload] = useLoad(() => api.slates(), []);
  return (
    <View state={list} reload={reload}>
      {(slates) => {
        const night = date ?? slates[0]?.date;
        return night ? <TopParlaysPanel date={night} checkToday /> : <p>No nights are loaded.</p>;
      }}
    </View>
  );
}
