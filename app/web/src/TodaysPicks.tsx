import { getTopContracts, type TopContract } from "./api";
import { Avatar } from "./Avatar";
import { View } from "./components";
import { stamp, pct, tipTime } from "./format";
import { isLegStat } from "./legTypes";
import { legHref } from "./router";
import type { ParlayState } from "./types";
import { betWords, evPerDollar, picksHead, priceCents, PICKS_FETCH, runIsForNight, selectPicks, whyText } from "./picksLogic";
import { useLoad } from "./useLoad";
import { Icon } from "./icons";
import { useMode, type Mode } from "./prefs";
import { instantMs } from "./time";

function Pick({ c, rank, mode }: { c: TopContract; rank: number; mode: Mode }) {
  const started = c.tip_utc !== null && instantMs(c.tip_utc) <= Date.now();
  return (
    <li class="tp-pick">
      <div class="tp-top">
        <Avatar name={c.player} team={c.team} playerId={c.player_id} size="md" remote />
        <div class="tp-body">
          <div class="tp-bet">
            <span class="tp-rank num" aria-hidden={c.ev > 0 ? "true" : undefined}>
              {c.ev > 0 ? rank : <span class="tag">not a bet</span>}
            </span>
            <strong>{betWords(c)}</strong>
          </div>
          <div class="small muted">
            {c.team !== null ? `${c.team} · ` : ""}
            {c.matchup}
            {c.tip_utc !== null ? ` · tip ${tipTime(c.tip_utc)}` : ""}
            {started ? " · game under way or over" : ""}
          </div>
        </div>
        {isLegStat(c.stat) ? (
          <a class="btn tp-open" href={legHref(c.game_id, c.player_id, c.stat, c.side)}>
            Open <span class="sr">{betWords(c)}</span>
            <Icon name="arrow" />
          </a>
        ) : null}
      </div>
      <dl class="tp-nums num">
        <div>
          <dt>Price</dt>
          <dd>{priceCents(c.price)}</dd>
        </div>
        <div>
          <dt>{c.context?.model_weight === 0 ? "Market middle (model weight 0)" : "Model chance"}</dt>
          <dd>{pct(c.model_prob)}</dd>
        </div>
        <div>
          <dt>EV per $1, after fees</dt>
          <dd class={c.ev > 0 ? "pos" : "neg"}>{evPerDollar(c.ev)}</dd>
        </div>
      </dl>
      {mode === "analyst" ? (
        <p class="small tp-why">{whyText(c)}</p>
      ) : (
        <details class="tp-more">
          <summary class="small">Why this number</summary>
          <p class="small tp-why">{whyText(c)}</p>
        </details>
      )}
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
  const mode = useMode();
  return (
    <section class="panel todays-picks" aria-labelledby="tp-h">
      <div class="label">Today&apos;s picks</div>
      <View state={top} reload={reload} what="the morning picks" onStale={props.onStale}>
        {(data) => {
          if (!runIsForNight(date, data.report_date)) {
            return (
              <>
                <h2 id="tp-h">Closest to break-even this morning (none is a bet)</h2>
                <p class="tp-none">
                  {data.report_date === null
                    ? "No morning run is stored yet."
                    : `The latest morning run is for ${data.report_date}, not this night, so no picks are shown for it.`}{" "}
                  Nothing to bet on here. Keep your money.
                </p>
              </>
            );
          }
          const shown = selectPicks(data.contracts);
          const head = picksHead(engine?.headline_verdict ?? null, engine?.counts["n_contracts"] ?? null, engine?.counts["n_flagged_positive"] ?? data.n_positive, shown);
          return (
            <>
              <h2 id="tp-h">{head.kind === "positive" ? "The model's morning picks" : "Closest to break-even this morning (none is a bet)"}</h2>
              <p class={`tp-head ${head.kind}`} role="status">
                {head.text}
              </p>
              {engine ? <p class="small muted">Prices as of {stamp(engine.as_of)}.</p> : null}
              {shown.length > 0 ? (
                <ol class="tp-list">
                  {shown.map((c, i) => (
                    <Pick key={`${c.ticker}|${c.side}`} c={c} rank={i + 1} mode={mode} />
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
