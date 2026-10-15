import { useState } from "preact/hooks";
import { api } from "./api";
import { american, bookName, lineText, num, pct } from "./format";
import { Explain, View } from "./components";
import { plainModel } from "./plain";
import { gameHref } from "./router";
import { percentAlong, modelLine, quantile, read, sliderRange, snapHalf } from "./slider";
import { Chips, DistStrip, ProbBar } from "./Viz";
import { wilson } from "./stats";
import { useLoad } from "./useLoad";
import type { CardStats, CardView } from "./types";
import { STAT_LABEL } from "./types";
import { sideText } from "./words";

/** The last ten games, oldest to newest, against tonight's line. Filled dot = went over. */
export function Sparkline(props: {
  values: readonly number[];
  line: number;
  label: string;
  games?: readonly { game_id: string; date: string; opp: string; value: number; linkable: boolean }[];
}) {
  const vals = [...props.values].reverse();
  const games = props.games ? [...props.games].reverse() : [];
  if (vals.length === 0) return null;
  const w = 220;
  const h = 46;
  const pad = 6;
  const lo = Math.min(...vals, props.line);
  const hi = Math.max(...vals, props.line);
  const span = hi - lo || 1;
  const x = (i: number): number => pad + (vals.length === 1 ? (w - 2 * pad) / 2 : (i / (vals.length - 1)) * (w - 2 * pad));
  const y = (v: number): number => h - pad - ((v - lo) / span) * (h - 2 * pad);
  const path = vals.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  return (
    <svg class="spark" viewBox={`0 0 ${w.toString()} ${h.toString()}`} role="img" aria-label={props.label}>
      <line x1="0" x2={w} y1={y(props.line)} y2={y(props.line)} class="spark-line" />
      <path d={path} class="spark-path" />
      {vals.map((v, i) => {
        const g = games[i];
        const dot = <circle cx={x(i)} cy={y(v)} r="3" class={v > props.line ? "spark-dot over" : "spark-dot"} />;
        return g?.linkable === true ? (
          <a key={i} href={gameHref(g.game_id)} class="spark-link" aria-label={`${g.date} vs ${g.opp}: ${v.toString()}. Open that game.`}>
            <circle cx={x(i)} cy={y(v)} r="9" class="spark-hit" />
            {dot}
          </a>
        ) : (
          <g key={i}>
            {g ? <title>{`${g.date} vs ${g.opp}: ${v.toString()} (not in the loaded data)`}</title> : null}
            {dot}
          </g>
        );
      })}
    </svg>
  );
}

function Facts(props: { s: CardStats; card: CardView }) {
  const { s, card } = props;
  const line = card.line;
  const h = s.hit10_over;
  const ci = h.n > 0 ? wilson(h.hits, h.n) : null;
  const avg = (v: number | null): string => (v === null ? "n/a" : num(v));
  return (
    <div class="facts">
      <p class="small muted">Games before {s.as_of_date} only. Nothing from tonight.</p>
      {s.prior_games < 10 ? (
        <p class="small">Only {s.prior_games} earlier games are on file, so read these loosely.</p>
      ) : null}
      <dl class="kvgrid">
        <div>
          <dt>Last 5</dt>
          <dd class="num">
            {s.last5.length ? s.last5.join(" · ") : "n/a"}
            <span class="muted"> avg {avg(s.avg5)}</span>
          </dd>
        </div>
        <div>
          <dt>Last 10</dt>
          <dd class="num">
            {s.last10.length ? s.last10.join(" · ") : "n/a"}
            <span class="muted"> avg {avg(s.avg10)}</span>
          </dd>
        </div>
        <div>
          <dt>Season average</dt>
          <dd class="num">
            {avg(s.avg_season)} <span class="muted">over {s.n_season} games</span>
          </dd>
        </div>
        <div>
          <dt>Minutes</dt>
          <dd class="num">
            {avg(s.min5)} <span class="muted">last 5</span> vs {avg(s.min_season)} <span class="muted">season</span>
          </dd>
        </div>
        <div>
          <dt>Over {lineText(line)} in last 10</dt>
          <dd class="num">
            {h.hits} of {h.n}
            {h.pushes > 0 ? <span class="muted"> ({h.pushes} push)</span> : null}
            {ci ? <span class="muted"> · 95% range {pct(ci[0])} to {pct(ci[1])}</span> : null}
          </dd>
        </div>
        <div>
          <dt>Opponent</dt>
          <dd>
            {card.home ? "vs" : "at"} {card.opponent}
          </dd>
        </div>
        <div>
          <dt>Teammates out</dt>
          <dd>
            {s.teammates_out.length > 0
              ? s.teammates_out.map((m) => `${m.name} (${m.status})`).join(", ")
              : s.teammates_known
                ? "none on the injury report"
                : "no injury report stored"}
          </dd>
        </div>
      </dl>
      <Sparkline
        values={s.last10}
        line={line}
        games={s.last10_games}
        label={`Last ${s.last10.length.toString()} games, oldest to newest: ${[...s.last10].reverse().join(", ")}. Line ${lineText(line)}.`}
      />
      <p class="small muted spark-key">
        Dots are the last {s.last10.length} games, oldest to newest. Filled means over {lineText(line)}. Dashed is the line.{" "}
        {s.last10_games.some((g) => g.linkable) ? "A tappable dot opens that game; older games are not in the loaded data." : "These games are older than the loaded data, so the dots do not open."}
      </p>
    </div>
  );
}

export function LineSlider({ card }: { card: CardView }) {
  const posted = card.line_source === "posted" ? card.line : null;
  const bookMain = card.book_main_line ?? card.rung_cal?.main_line ?? null;
  const input = {
    p_ge: card.dist.p_ge,
    method: card.dist.method,
    posted_line: posted,
    book_main_line: bookMain,
    rung_cal: card.rung_cal,
  };
  const model = modelLine(card.dist.p_ge);
  // Reach: from just under the 0.5th percentile to just over the 99.5th, widened to hold the marks.
  const reachLo = Math.max(card.dist.line_min, quantile(card.dist.p_ge, 0.005) - 0.5);
  const reachHi = Math.min(card.dist.line_max, quantile(card.dist.p_ge, 0.995) + 1.5);
  const { min, max } = sliderRange(reachLo, Math.max(reachLo, reachHi), [posted, bookMain, model]);
  const start = snapHalf(posted ?? model);
  const [line, setLine] = useState(Math.min(max, Math.max(min, start)));
  const r = read(input, line);
  const id = `slider-${card.prop_id}`;
  const stat = card.stat;
  const step = (d: number): void => {
    setLine((l) => Math.min(max, Math.max(min, l + d)));
  };
  const marks: { at: number; cls: string; name: string }[] = [];
  if (posted !== null) marks.push({ at: posted, cls: "posted", name: "posted line" });
  if (bookMain !== null && bookMain !== posted) marks.push({ at: bookMain, cls: "book", name: "book main line" });
  marks.push({ at: model, cls: "model", name: "model's line" });
  return (
    <div class="line-slider">
      <div class="ls-head">
        <label class="label" for={id}>
          {STAT_LABEL[stat]} line
        </label>
        <div class="ls-tags">
          {r.is_posted ? <span class="tag posted">posted line</span> : null}
          {r.is_book_main && !r.is_posted ? <span class="tag book">book main line</span> : null}
          {r.is_model_line ? <span class="tag model">model's line</span> : null}
        </div>
      </div>
      <div class="ls-controls">
        <button type="button" class="btn" aria-label="Lower the line by one" disabled={line <= min} onClick={() => { step(-1); }}>
          -1
        </button>
        <output class="line-out num" htmlFor={id}>
          {lineText(line)}
        </output>
        <button type="button" class="btn" aria-label="Raise the line by one" disabled={line >= max} onClick={() => { step(1); }}>
          +1
        </button>
      </div>
      <div class="ls-track">
        <input
          id={id}
          class="slider"
          type="range"
          min={min}
          max={max}
          step={1}
          value={line}
          aria-valuetext={`line ${lineText(line)}`}
          onInput={(e) => {
            setLine(snapHalf(Number(e.currentTarget.value)));
          }}
        />
        <div class="ls-marks" aria-hidden="true">
          {marks.map((m) => (
            <i key={m.cls} class={`ls-mark ${m.cls}`} style={{ left: `${percentAlong(m.at, min, max).toString()}%` }} />
          ))}
        </div>
      </div>
      <div class="ls-jump small">
        {marks.map((m) => (
          <button
            key={m.cls}
            type="button"
            class={`linklike ls-${m.cls}`}
            onClick={() => { setLine(Math.min(max, Math.max(min, snapHalf(m.at === Math.floor(m.at) ? m.at - 0.5 : m.at)))); }}
          >
            {m.name} <span class="num">{lineText(m.at)}</span>
          </button>
        ))}
      </div>
      <DistStrip pge={card.dist.p_ge} line={line} model={model} unit={STAT_LABEL[stat].toLowerCase()} />
      <p class="small muted num ds-key">
        Bars are the model's chance of each count. Shaded: the middle 80% ({quantile(card.dist.p_ge, 0.1)} to {quantile(card.dist.p_ge, 0.9)}).
        Solid line: your line. Dotted: the model's line. Accent bars: over.
      </p>
      <div class="ls-read" aria-live="polite">
        <div class="ls-side">
          <div class="big-num num">{pct(r.p_over)}</div>
          <div class="alt-tag">over</div>
        </div>
        <div class="ls-side">
          <div class="big-num num">{pct(r.p_under)}</div>
          <div class="alt-tag">under</div>
        </div>
      </div>
      <ProbBar over={r.p_over} label={`At line ${lineText(line)}`} />
      <p class="small ls-say">
        Over {lineText(line)} means {sideText(stat, line, "over")}. Under means {sideText(stat, line, "under")}.
      </p>
      <p class={`small ls-label ${r.source}`}>{r.plain}</p>
      <Explain label="Show the exact details">
        <p class="small">{r.label}</p>
        {card.dist.method === "exact" && card.dist.n !== null ? <p class="small">Forecast table resolution: 1 in {card.dist.n}.</p> : null}
      </Explain>
      {r.tail_note ? <p class="small ls-tail">{r.tail_note}</p> : null}
      <p class="small muted num">
        {posted !== null && card.over_price !== null && card.under_price !== null
          ? `Posted line ${lineText(posted)}${card.price_book ? ` at ${bookName(card.price_book)}` : ""}: over ${american(card.over_price)}, under ${american(card.under_price)}. Prices exist only at the posted line.`
          : "No posted price is stored for this prop."}
      </p>
    </div>
  );
}

/** Opens on demand. Opening it shows our forecast, so the pick is recorded as not blind. */
export function CardDetail(props: { card: { game_id: string; player_id: number; stat: string }; onReveal: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      class="card-detail"
      onToggle={(e) => {
        const now = e.currentTarget.open;
        setOpen(now);
        if (now) props.onReveal();
      }}
    >
      <summary>Stats and line slider</summary>
      {open ? <Body {...props.card} /> : null}
    </details>
  );
}

function Body(props: { game_id: string; player_id: number; stat: string }) {
  const [state, reload] = useLoad(() => api.card(props.game_id, props.player_id, props.stat), [props.game_id, props.player_id, props.stat]);
  return (
    <View state={state} reload={reload}>
      {(card) => (
        <div class="card-detail-body">
          <p class="small muted">
            Opening this shows our forecast. Your pick is then saved as not blind. Nothing here is advice, and no
            value on the slider is a bet recommendation.
          </p>
          <Chips model={plainModel(card.model_source)} stampText={card.forecast_stamp} tipUtc={card.tip_utc} extra={card.dist.method === "exact" ? [] : ["rougher estimate"]} />
          <Explain label="Show the exact details">
            <ul class="plain small">
              <li>{card.model_label}</li>
              <li>Forecast stamp: <span class="mono">{card.forecast_stamp}</span></li>
              <li>{card.dist.method === "exact" ? "Read from the exact count table." : "Read off a 19-point quantile grid."}</li>
              {card.rung_cal ? <li>Calibration map {card.rung_cal.map_version}.</li> : null}
            </ul>
          </Explain>
          {card.stats ? <Facts s={card.stats} card={card} /> : <p class="small">No game history is loaded for this prop.</p>}
          <LineSlider card={card} />
        </div>
      )}
    </View>
  );
}
