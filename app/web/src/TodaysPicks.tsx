import { getTopContracts, type TopContract } from "./api";
import { Avatar } from "./Avatar";
import { View } from "./components";
import { stamp, pct, tipTime } from "./format";
import { isLegStat } from "./legTypes";
import { legHref } from "./router";
import type { ParlayState } from "./types";
import { betWords, evPerDollar, picksHead, priceCents, PICKS_FETCH, runIsForNight, selectPicks, whyText } from "./picksLogic";
import { useLoad } from "./useLoad";

function Pick({ c, rank }: { c: TopContract; rank: number }) {
  const started = c.tip_utc !== null && Date.parse(c.tip_utc) <= Date.now();
  return (
    <li class="tp-pick">
      <span class="tp-rank num" aria-hidden="true">
        {rank}
      </span>
      <Avatar name={c.player} team={c.team} playerId={c.player_id} size="md" />
      <div class="tp-body">
        <div class="tp-bet">
          <strong>{betWords(c)}</strong>
        </div>
        <div class="small muted">
          {c.team !== null ? `${c.team} · ` : ""}
          {c.matchup}
          {c.tip_utc !== null ? ` · tip ${tipTime(c.tip_utc)}` : ""}
          {started ? " · game under way or over" : ""}
        </div>
        <p class="small tp-why">{whyText(c)}</p>
      </div>
      <dl class="tp-nums num">
        <div>
          <dt>Price</dt>
          <dd>{priceCents(c.price)}</dd>
        </div>
        <div>
          <dt>Model chance</dt>
          <dd>{pct(c.model_prob)}</dd>
        </div>
        <div>
          <dt>EV per $1, after fees</dt>
          <dd class={c.ev > 0 ? "pos" : "neg"}>{evPerDollar(c.ev)}</dd>
        </div>
      </dl>
      {isLegStat(c.stat) ? (
        <a class="small tp-open" href={legHref(c.game_id, c.player_id, c.stat, c.side)}>
          Open <span class="sr">{betWords(c)}</span>
        </a>
      ) : null}
    </li>
  );
}

/**
 * Today's picks: the morning run's closest contracts by edge after fees. Says plainly when none is positive (the usual
 * case). Shows no outcome anywhere, so no model chance ever sits beside a result (LINE_CAL_LIVE). Paper only; no stakes.
 */
export function TodaysPicks(props: { date: string; engine: ParlayState | null; onStale?: (s: boolean) => void }) {
  const { date, engine } = props;
  const [top, reload] = useLoad(() => getTopContracts(PICKS_FETCH), []);
  return (
    <section class="panel todays-picks" aria-labelledby="tp-h">
      <div class="label">Today&apos;s picks</div>
      <h2 id="tp-h">The model&apos;s morning picks</h2>
      <View state={top} reload={reload} what="the morning picks" onStale={props.onStale}>
        {(data) => {
          if (!runIsForNight(date, data.report_date)) {
            return (
              <p class="tp-none">
                {data.report_date === null
                  ? "No morning run is stored yet."
                  : `The latest morning run is for ${data.report_date}, not this night, so no picks are shown for it.`}{" "}
                Nothing to bet on here. Keep your money.
              </p>
            );
          }
          const shown = selectPicks(data.contracts);
          const head = picksHead(engine?.headline_verdict ?? null, engine?.counts["n_contracts"] ?? null, engine?.counts["n_flagged_positive"] ?? data.n_positive, shown);
          return (
            <>
              <p class={`tp-head ${head.kind}`} role="status">
                {head.text}
              </p>
              {engine ? <p class="small muted">Morning run, prices as of {stamp(engine.as_of)}.</p> : null}
              {shown.length > 0 ? (
                <ol class="tp-list">
                  {shown.map((c, i) => (
                    <Pick key={`${c.ticker}|${c.side}`} c={c} rank={i + 1} />
                  ))}
                </ol>
              ) : null}
              <p class="small muted">Paper only, not advice. No stake sizes. Prices are the ask on Kalshi with the taker fee.</p>
            </>
          );
        }}
      </View>
    </section>
  );
}
