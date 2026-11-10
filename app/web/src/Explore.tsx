import { useState } from "preact/hooks";
import { api } from "./api";
import { Explain, View } from "./components";
import { american, lineText, pct } from "./format";
import type { Lens } from "./prefs";
import { evVerdict } from "./plain";
import { hideBook, modelLine, calibratedLine, percentAlong, quantile, read, sliderRange, snapHalf } from "./slider";
import { STAT_LABEL, type CardView, type MinutesInfo } from "./types";
import { SitsPicker } from "./TeammateSits";
import { useLoad } from "./useLoad";
import { DistStrip, ProbBar } from "./Viz";
import { sideText } from "./words";
import {
  bookChance,
  minutesWhatIf,
  momentsOf,
  overAt,
  parseOdds,
  priceRead,
  type FeeKind,
} from "./whatif";

const S = (n: number): string => n.toString();
const signedMoney = (x: number): string => `${x < 0 ? "-" : "+"}$${Math.abs(x).toFixed(2)}`;

/** The minutes block: his projection, the slider, and how much of the spread minutes explain. */
function MinutesBlock(props: {
  mi: MinutesInfo;
  minutes: number;
  narrow: boolean;
  onMinutes: (m: number) => void;
  onNarrow: (v: boolean) => void;
}) {
  const { mi } = props;
  const base = mi.baseline;
  if (base === null || !(base > 0)) {
    return (
      <div class="xp-block">
        <h3>If he plays a different number of minutes</h3>
        <p class="small muted">This data holds no usable projected or recent minutes for him, so the minutes what-if is switched off here rather than guessing.</p>
      </div>
    );
  }
  const where = mi.baseline_source === "model" ? `the model's projection, ${base.toFixed(0)} minutes` : `his recent average, ${base.toFixed(0)} minutes (this data holds no projection)`;
  const share = mi.minutes_share;
  const [lo, hi] = mi.range_now;
  return (
    <div class="xp-block">
      <h3>If he plays a different number of minutes</h3>
      <p class="small muted">Starts from {where}. Assumes he plays.</p>
      <div class="ls-controls">
        <button type="button" class="btn" aria-label="One fewer minute" disabled={props.minutes <= 4} onClick={() => { props.onMinutes(props.minutes - 1); }}>
          -1
        </button>
        <output class="line-out num" htmlFor="xp-min" aria-live="polite">
          {S(props.minutes)} min
        </output>
        <button type="button" class="btn" aria-label="One more minute" disabled={props.minutes >= 48} onClick={() => { props.onMinutes(props.minutes + 1); }}>
          +1
        </button>
      </div>
      <input id="xp-min" class="slider" type="range" min={4} max={48} step={1} value={props.minutes} aria-valuetext={`${S(props.minutes)} minutes`} onInput={(e) => { props.onMinutes(Number(e.currentTarget.value)); }} />
      <div class="ls-jump small">
        <button type="button" class="linklike" onClick={() => { props.onMinutes(Math.round(base)); }}>
          {mi.baseline_source === "model" ? "model's projection" : "recent average"} <span class="num">{base.toFixed(0)}</span>
        </button>
        {mi.recent_avg !== null && mi.baseline_source === "model" ? (
          <button type="button" class="linklike" onClick={() => { props.onMinutes(Math.round(mi.recent_avg ?? base)); }}>
            last {S(mi.recent_n)} games, average <span class="num">{mi.recent_avg.toFixed(0)}</span>
          </button>
        ) : null}
      </div>
      <p class="small">
        <b>Method, and it is an approximation:</b> we stretch or shrink the forecast by minutes divided by {base.toFixed(0)}, as if his
        per-minute production stayed the same. Real rates move with the role and the game, so read this as a feel for the
        size of the effect, not a forecast.
      </p>
      <div class="xp-share">
        <h4>How much of the uncertainty is minutes?</h4>
        {share !== null ? (
          <>
            <p class="num">
              <span class="big-num">about {Math.round(share * 100)}%</span>
              <span class="muted small"> of the spread in {mi.stat_mean !== null ? "this stat" : "the stat"} (rough)</span>
            </p>
            <div class="xp-bar" role="img" aria-label={`Minutes account for about ${Math.round(share * 100).toString()} percent of the spread`}>
              <i style={{ width: `${(share * 100).toFixed(1)}%` }} />
            </div>
            <p class="small num">
              The model's middle 80% runs {lo.toFixed(1)} to {hi.toFixed(1)}.{" "}
              {mi.range_minutes_known
                ? `If minutes were known exactly it would be about ${mi.range_minutes_known[0].toFixed(1)} to ${mi.range_minutes_known[1].toFixed(1)}.`
                : ""}
            </p>
            <label class="xp-check">
              <input type="checkbox" checked={props.narrow} onChange={(e) => { props.onNarrow(e.currentTarget.checked); }} /> Treat minutes as known (narrow the spread by that share)
            </label>
          </>
        ) : (
          <p class="small muted">
            Not enough earlier games to estimate it: this needs at least 10 games of his minutes and he has {S(mi.recent_n)} here.
          </p>
        )}
        <Explain label="How this is worked out">
          <p class="small">{mi.method}</p>
          <p class="small">
            Minutes spread: {mi.recent_sd !== null ? `${mi.recent_sd.toFixed(1)} minutes either side of ${(mi.recent_avg ?? 0).toFixed(1)}` : "not available"}, over his last {S(mi.recent_n)} games.
            The share is that spread, as a fraction of his average minutes, times the stat's average, divided by the stat's own spread, squared.
            It cannot say what drives his minutes (injuries, blowouts, foul trouble).
          </p>
        </Explain>
      </div>
    </div>
  );
}

/** Type a price and a fee: break-even chance and EV per $1 against the model's number. */
function PriceBlock(props: { card: CardView; q: number | null; side: "over" | "under"; onSide: (s: "over" | "under") => void; qLabel: string }) {
  const { card } = props;
  const fmt = (n: number | null): string => (n === null ? "" : n > 0 ? `+${S(n)}` : S(n));
  const [overText, setOver] = useState("");
  const [underText, setUnder] = useState("");
  const [kind, setKind] = useState<FeeKind>("none");
  const [custom, setCustom] = useState("0");
  const [parlay] = useLoad(() => api.parlay(), []);
  const fee = parlay.status === "ready" ? parlay.data.fee_model : null;
  const coefficient = fee?.coefficient ?? null;
  const typed = parseOdds(props.side === "over" ? overText : underText);
  const both = parseOdds(overText) !== null && parseOdds(underText) !== null ? bookChance(parseOdds(overText) ?? 0, parseOdds(underText) ?? 0) : null;
  const read_ = typed === null || props.q === null ? null : priceRead(props.q, typed, kind, coefficient, Number(custom) || 0);
  const posted = card.line_source === "posted" && card.over_price !== null && card.under_price !== null;
  return (
    <div class="xp-block">
      <h3>Price and fees</h3>
      <p class="small muted">Type the price you were shown. This is arithmetic on your number, not a recommendation to bet.</p>
      <div class="xp-fields">
        <label class="field">
          <span class="label">Over price</span>
          <input type="text" inputMode="numeric" autoComplete="off" placeholder="-110" value={overText} onInput={(e) => { setOver(e.currentTarget.value); }} />
        </label>
        <label class="field">
          <span class="label">Under price (optional)</span>
          <input type="text" inputMode="numeric" autoComplete="off" placeholder="-110" value={underText} onInput={(e) => { setUnder(e.currentTarget.value); }} />
        </label>
      </div>
      {posted ? (
        <button
          type="button"
          class="linklike small"
          onClick={() => {
            setOver(fmt(card.over_price));
            setUnder(fmt(card.under_price));
          }}
        >
          Use the stored posted prices ({american(card.over_price ?? 0)} / {american(card.under_price ?? 0)} at {lineText(card.line)})
        </button>
      ) : null}
      <div class="seg wide" role="group" aria-label="Which side is the price for">
        {(["over", "under"] as const).map((s) => (
          <button key={s} type="button" class="seg-btn" aria-pressed={props.side === s} onClick={() => { props.onSide(s); }}>
            Price is for the {s}
          </button>
        ))}
      </div>
      <fieldset class="xp-fee">
        <legend class="label">Fee</legend>
        <div class="seg wide" role="group" aria-label="Fee">
          <button type="button" class="seg-btn" aria-pressed={kind === "none"} onClick={() => { setKind("none"); }}>
            None
          </button>
          {fee ? (
            <button type="button" class="seg-btn" aria-pressed={kind === "schedule"} onClick={() => { setKind("schedule"); }}>
              Exchange schedule
            </button>
          ) : null}
          <button type="button" class="seg-btn" aria-pressed={kind === "custom"} onClick={() => { setKind("custom"); }}>
            My own
          </button>
        </div>
        {kind === "schedule" && fee ? <p class="small muted">{fee.formula} ({fee.source}). Not rounded up to the cent, so a shade low.</p> : null}
        {kind === "custom" ? (
          <label class="field">
            <span class="label">Cents per $1 of payout</span>
            <input type="text" inputMode="decimal" value={custom} onInput={(e) => { setCustom(e.currentTarget.value); }} />
          </label>
        ) : null}
      </fieldset>
      {read_ === null ? (
        <p class="small muted" aria-live="polite">
          {overText === "" && underText === "" ? "Enter a price like -110 or +150 to see the arithmetic." : "That does not look like an American price (like -110 or +150)."}
        </p>
      ) : (
        <div class="xp-read" aria-live="polite">
          <dl class="kvgrid">
            <div>
              <dt>The price alone implies</dt>
              <dd class="num">{pct(read_.implied, 1)}</dd>
            </div>
            {both !== null ? (
              <div>
                <dt>Book's own chance (margin removed)</dt>
                <dd class="num">{pct(both, 1)} over</dd>
              </div>
            ) : null}
            <div>
              <dt>Break-even chance{read_.fee > 0 ? " (price plus fee)" : ""}</dt>
              <dd class="num">{pct(read_.breakEven, 1)}</dd>
            </div>
            <div>
              <dt>{props.qLabel}</dt>
              <dd class="num">{props.q === null ? "no calibrated number at this line" : pct(props.q, 1)}</dd>
            </div>
            <div>
              <dt>Gap (model minus break-even)</dt>
              <dd class="num">
                {read_.gap >= 0 ? "+" : "-"}
                {Math.abs(read_.gap * 100).toFixed(1)} points
              </dd>
            </div>
            <div>
              <dt>EV per $1, if the model were exactly right</dt>
              <dd class="num">{signedMoney(read_.ev)}</dd>
            </div>
          </dl>
          <p class="small">{evVerdict(read_.ev, null)}.</p>
          <p class="small muted">
            Our props model has no live record yet (see the Track record), so its number can be off in either direction. A positive
            figure here is a feature of the inputs you typed, not a signal. Analysis only, not advice.
          </p>
        </div>
      )}
    </div>
  );
}

function Soon(props: { title: string; body: string }) {
  return (
    <div class="xp-soon" role="note">
      <div class="row between wrap">
        <strong>{props.title}</strong>
        <span class="tag">coming soon</span>
      </div>
      <p class="small muted">{props.body}</p>
    </div>
  );
}

function Lab({ card, lens }: { card: CardView; lens: Lens }) {
  const pge = card.dist.p_ge;
  const mi = card.minutes ?? null;
  const baseline = mi?.baseline ?? null;
  const posted = card.line_source === "posted" ? card.line : null;
  const bookMain = card.book_main_line ?? card.line_cal?.main_line ?? card.rung_cal?.main_line ?? null;
  const model = modelLine(pge);
  const base = momentsOf(pge);
  const [minutes, setMinutes] = useState(baseline === null ? 0 : Math.round(baseline));
  const [narrow, setNarrow] = useState(false);
  const [priceOn, setPriceOn] = useState(false);
  const [side, setSide] = useState<"over" | "under">("over");
  const unchanged = baseline === null || (!narrow && minutes === Math.round(baseline));
  const known = narrow && mi?.minutes_share != null ? { share: mi.minutes_share, mean: base.mean } : null;
  const dist = baseline !== null && !unchanged ? minutesWhatIf(pge, baseline, minutes, known) : pge;
  const wide = baseline === null ? pge : minutesWhatIf(pge, baseline, Math.min(48, baseline * 1.5), null);
  const slim = baseline === null ? pge : minutesWhatIf(pge, baseline, baseline * 0.5, null);
  const reachLo = Math.max(0.5, quantile(slim, 0.005) - 0.5);
  const reachHi = Math.max(reachLo, quantile(wide, 0.995) + 1.5);
  const { min, max } = sliderRange(reachLo, reachHi, [posted, bookMain, model]);
  const [line, setLine] = useState(Math.min(max, Math.max(min, snapHalf(posted ?? model))));
  const input = {
    p_ge: pge,
    method: card.dist.method,
    posted_line: posted,
    book_main_line: bookMain,
    rung_cal: card.rung_cal,
    line_cal: card.line_cal ?? null,
    line_cal_null_reason: card.line_cal_null_reason ?? null,
  };
  const r = read(input, line);
  const pOver: number | null = unchanged ? r.p_over : overAt(dist, line);
  const pUnder = pOver === null ? null : 1 - pOver;
  const moved = momentsOf(dist);
  const id = `xp-line-${card.prop_id}`;
  const stat = card.stat;
  const step = (d: number): void => {
    setLine((l) => Math.min(max, Math.max(min, l + d)));
  };
  const marks: { at: number; name: string }[] = [];
  if (posted !== null) marks.push({ at: posted, name: "posted line" });
  marks.push({ at: model, name: "model's line" });
  const showPrice = lens === "market" || priceOn;
  const calLine = unchanged ? calibratedLine(r) : null;
  const qSide: number | null = side === "over" ? pOver : pUnder;
  return (
    <div class="stack tight">
      <div class="xp-block">
        <h3>Move the line</h3>
        <div class="ls-controls">
          <button type="button" class="btn" aria-label="Lower the line by one" disabled={line <= min} onClick={() => { step(-1); }}>
            -1
          </button>
          <output class="line-out num" htmlFor={id} aria-live="polite">
            {lineText(line)}
          </output>
          <button type="button" class="btn" aria-label="Raise the line by one" disabled={line >= max} onClick={() => { step(1); }}>
            +1
          </button>
        </div>
        <div class="ls-track">
          <input id={id} class="slider" type="range" min={min} max={max} step={1} value={line} aria-valuetext={`line ${lineText(line)}`} onInput={(e) => { setLine(snapHalf(Number(e.currentTarget.value))); }} />
          <div class="ls-marks" aria-hidden="true">
            {marks.map((m) => (
              <i key={m.name} class={`ls-mark ${m.name === "posted line" ? "posted" : "model"}`} style={{ left: `${percentAlong(m.at, min, max).toString()}%` }} />
            ))}
          </div>
        </div>
        <div class="ls-jump small">
          {marks.map((m) => (
            <button key={m.name} type="button" class="linklike" onClick={() => { setLine(Math.min(max, Math.max(min, snapHalf(m.at === Math.floor(m.at) ? m.at - 0.5 : m.at)))); }}>
              {m.name} <span class="num">{lineText(m.at)}</span>
            </button>
          ))}
        </div>
        <DistStrip pge={dist} line={line} model={unchanged ? model : null} unit={STAT_LABEL[stat].toLowerCase()} />
        <p class="small muted num ds-key">
          Bars are the chance of each count. Shaded: the middle 80%. Solid line: your line. Accent bars: over.
          {unchanged ? "" : ` Typical night now ${moved.mean.toFixed(1)}, was ${base.mean.toFixed(1)}.`}
        </p>
        {pOver !== null && pUnder !== null ? (
          <>
            <div class="ls-read" aria-live="polite">
              <div class="ls-side">
                <div class="big-num num">{pct(pOver)}</div>
                <div class="alt-tag">over</div>
              </div>
              <div class="ls-side">
                <div class="big-num num">{pct(pUnder)}</div>
                <div class="alt-tag">under</div>
              </div>
            </div>
            <ProbBar over={pOver} label={`At line ${lineText(line)}`} />
          </>
        ) : (
          <p class="ls-read ls-nocal" aria-live="polite">
            <b>No calibrated number at this line.</b> Step back to the calibrated line to read a chance; the bars above are the forecast itself.
          </p>
        )}
        <p class="small ls-say">
          Over {lineText(line)} means {sideText(stat, line, "over")}. Under means {sideText(stat, line, "under")}.
        </p>
        {unchanged ? (
          <>
            <div class="ls-pair small num">
              {calLine !== null ? <div class="ls-cal">{lens === "market" ? calLine : hideBook(calLine)}</div> : <div class="ls-cal none muted">{lens === "market" ? r.cal_note : hideBook(r.cal_note ?? "")}</div>}
            </div>
            <p class={`small ls-label ${r.source}`}>{r.plain}</p>
            {r.tail_note ? <p class="small ls-tail">{r.tail_note}</p> : null}
          </>
        ) : (
          <p class="small ls-label model">
            Scaled model: the stored forecast stretched to {S(minutes)} minutes{known ? ", spread narrowed" : ""}. The calibration was fitted to the
            unscaled forecast, so it does not apply here: this number is model only, and rougher.
          </p>
        )}
      </div>

      {mi ? <MinutesBlock mi={mi} minutes={minutes} narrow={narrow} onMinutes={(m) => { setMinutes(Math.min(48, Math.max(4, m))); }} onNarrow={setNarrow} /> : null}

      {showPrice ? (
        <PriceBlock card={card} q={qSide} side={side} onSide={setSide} qLabel={`Model's chance of the ${side}${unchanged ? "" : " (scaled)"}`} />
      ) : (
        <div class="xp-block">
          <h3>Price and fees</h3>
          <p class="small muted">Off by default in this lens: Explore is about the forecast. Turn it on to type a price and a fee.</p>
          <button type="button" class="btn" onClick={() => { setPriceOn(true); }}>
            Show the price arithmetic
          </button>
        </div>
      )}

      {lens === "explore" ? (
        <>
          <SitsPicker gameId={card.game_id} playerId={card.player_id} playerName={card.player} />
          <div class="xp-soon-row">
            <Soon title="Combos" body="Points plus assists, and other sums, with the two stats moving together instead of being added as if independent. The math for this arrives later." />
          </div>
        </>
      ) : null}
    </div>
  );
}

/** The what-if lab for one player and stat: line, minutes and (opt-in or in the Market lens) price and fees. */
export function ExplorePanel(props: { gameId: string; playerId: number; stat: string; lens: Lens }) {
  const [state, reload] = useLoad(() => api.card(props.gameId, props.playerId, props.stat), [props.gameId, props.playerId, props.stat]);
  return (
    <section class="panel explore" aria-label="What if">
      <h2>{props.lens === "market" ? "Price and what-ifs" : "What if?"}</h2>
      <p class="small muted">Everything below is worked out in your browser from the stored forecast. Nothing is saved or scored.</p>
      <View state={state} reload={reload}>
        {(card) => <Lab key={card.prop_id} card={card} lens={props.lens} />}
      </View>
    </section>
  );
}
