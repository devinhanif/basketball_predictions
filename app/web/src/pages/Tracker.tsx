import { useEffect, useMemo, useState } from "preact/hooks";
import { api, scanScreenshot } from "../api";
import type { KalshiMarket, LiveGame, LiveGameRow } from "../liveTypes";
import { FC_STATS, type FcStat } from "../liveTypes";
import { SourceLine } from "./Live";
import { CsvImport, OptionsView } from "./TrackerCharts";
import { ImportPreview, type PreviewSource } from "./ImportPreview";
import { decodeImport } from "../importLink";
import type { History } from "../optionsChart";
import { usePoll } from "../usePoll";
import {
  STAT_NAME,
  browserStore,
  comboCheck,
  describeLeg,
  exitSignal,
  fullHedgeCount,
  hedge,
  legStatus,
  loadParlays,
  mid,
  money,
  paidOf,
  priceOf,
  saveParlays,
  summarize,
  withMarket,
  type Leg,
  type LegModel,
  type LegKind,
  type LegRow,
  type LegSide,
  type Parlay,
} from "../tracker";

const uid = (): string => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

interface Feed {
  games: Record<string, LiveGame | null>;
  markets: Record<string, KalshiMarket>;
  kalshiError: string | null;
  gamesFailed: number;
  stale: boolean;
}

/* ------------------------------------------------------------------ small controls */
function NumField(props: { label: string; value: number | null; onChange: (v: number | null) => void; step?: string; hint?: string }) {
  const [text, setText] = useState(props.value === null ? "" : props.value.toString());
  useEffect(() => {
    const cur = text.trim() === "" ? null : Number(text);
    if (cur !== props.value && !(cur !== null && Number.isNaN(cur))) setText(props.value === null ? "" : props.value.toString());
  }, [props.value]);
  return (
    <label class="tk-field">
      <span class="label">{props.label}</span>
      <input
        type="number"
        inputMode="decimal"
        min="0"
        step={props.step ?? "any"}
        value={text}
        onInput={(e) => {
          const t = e.currentTarget.value;
          setText(t);
          const n = t.trim() === "" ? null : Number(t);
          props.onChange(n === null || !Number.isFinite(n) || n < 0 ? null : n);
        }}
      />
      {props.hint ? <span class="small muted">{props.hint}</span> : null}
    </label>
  );
}

const STATE_TEXT = { pre: "not started", live: "live", done: "done", dead: "dead", unknown: "unknown" } as const;

function cents(x: number | null): string {
  return x === null ? "–" : `${Math.round(x * 100).toString()}¢`;
}

/* ------------------------------------------------------------------ add a leg */
function AddLeg(props: { games: LiveGameRow[]; onAdd: (l: Leg) => void }) {
  const [eventId, setEventId] = useState("");
  const [kind, setKind] = useState<LegKind>("player");
  const [player, setPlayer] = useState("");
  const [stat, setStat] = useState<FcStat>("pts");
  const [side, setSide] = useState<LegSide>("over");
  const [line, setLine] = useState<number | null>(null);
  const [team, setTeam] = useState("");
  const [ticker, setTicker] = useState("");
  const [entry, setEntry] = useState<number | null>(null);
  const [check, setCheck] = useState<string | null>(null);
  const game = props.games.find((g) => g.event_id === eventId);
  const needsTeam = kind === "win" || kind === "spread";
  const needsLine = kind !== "win";
  const problem =
    !game
      ? "Pick a game."
      : kind === "player" && player.trim() === ""
        ? "Enter the player's name."
        : needsTeam && team === ""
          ? "Pick a team."
          : needsLine && (line === null || (kind === "spread" ? false : line <= 0))
            ? "Enter the line."
            : null;
  const add = (): void => {
    if (!game || problem) return;
    props.onAdd({
      id: uid(),
      kind,
      eventId,
      gameLabel: `${game.away.abbr} @ ${game.home.abbr}`,
      startUtc: game.start_utc,
      player: player.trim(),
      stat,
      side,
      line: kind === "win" ? 0 : (line ?? 0),
      team,
      ticker: ticker.trim(),
      entry,
    });
    setPlayer("");
    setLine(null);
    setTicker("");
    setEntry(null);
    setCheck(null);
  };
  const verify = (): void => {
    const t = ticker.trim();
    if (t === "") return;
    setCheck("Checking Kalshi's public data...");
    api.kalshi([t]).then(
      (r) => {
        const m = r.data.markets[0];
        setCheck(
          m?.found
            ? `${m.title || m.ticker}: yes bid ${cents(m.yes_bid)}, ask ${cents(m.yes_ask)} (${m.status || "status unknown"})`
            : "Kalshi has no public market with that ticker.",
        );
      },
      () => {
        setCheck("Kalshi's public data did not answer. You can still add the leg and prices will appear when it does.");
      },
    );
  };
  return (
    <details class="tk-add">
      <summary class="btn">Add a leg</summary>
      <div class="tk-form">
        <label class="tk-field">
          <span class="label">Game</span>
          <select value={eventId} onChange={(e) => { setEventId(e.currentTarget.value); setTeam(""); }}>
            <option value="">Pick a game from today's scoreboard</option>
            {props.games.map((g) => (
              <option key={g.event_id} value={g.event_id}>
                {g.away.abbr} @ {g.home.abbr} ({g.status.state === "pre" ? "upcoming" : g.status.state === "in" ? "live" : "final"})
              </option>
            ))}
          </select>
        </label>
        <div class="st-seg-row" role="group" aria-label="Kind of leg">
          {(["player", "win", "spread", "total"] as const).map((k) => (
            <button key={k} type="button" class="st-chip" aria-pressed={kind === k} onClick={() => { setKind(k); }}>
              {k === "player" ? "Player stat" : k === "win" ? "Team win" : k === "spread" ? "Spread" : "Total"}
            </button>
          ))}
        </div>
        {kind === "player" ? (
          <>
            <label class="tk-field">
              <span class="label">Player (as ESPN spells it)</span>
              <input type="text" value={player} autocomplete="off" onInput={(e) => { setPlayer(e.currentTarget.value); }} />
            </label>
            <label class="tk-field">
              <span class="label">Stat</span>
              <select value={stat} onChange={(e) => { setStat(e.currentTarget.value as FcStat); }}>
                {FC_STATS.map((s) => (
                  <option key={s} value={s}>{STAT_NAME[s]}</option>
                ))}
              </select>
            </label>
          </>
        ) : null}
        {needsTeam ? (
          <label class="tk-field">
            <span class="label">Team</span>
            <select value={team} onChange={(e) => { setTeam(e.currentTarget.value); }}>
              <option value="">Pick a team</option>
              {game ? [game.away.abbr, game.home.abbr].map((t) => <option key={t} value={t}>{t}</option>) : null}
            </select>
          </label>
        ) : null}
        {kind === "player" || kind === "total" ? (
          <div class="st-seg-row" role="group" aria-label="Over or under">
            {(["over", "under"] as const).map((s) => (
              <button key={s} type="button" class="st-chip" aria-pressed={side === s} onClick={() => { setSide(s); }}>
                {kind === "player" ? (s === "over" ? "N or more" : "Fewer than N") : s === "over" ? "Over" : "Under"}
              </button>
            ))}
          </div>
        ) : null}
        {needsLine ? (
          <NumField
            label={kind === "player" ? "N (the number needed)" : kind === "total" ? "Line (e.g. 224.5)" : "Handicap (e.g. -5.5 or 3.5)"}
            value={line}
            onChange={setLine}
            hint={kind === "spread" ? "Negative means the team must win by more than that." : undefined}
          />
        ) : null}
        <label class="tk-field">
          <span class="label">Kalshi ticker (optional, for live prices)</span>
          <input type="text" value={ticker} autocomplete="off" autocapitalize="characters" onInput={(e) => { setTicker(e.currentTarget.value); }} />
          <button type="button" class="linkbtn" onClick={verify} disabled={ticker.trim() === ""}>Check this ticker</button>
          {check ? <span class="small muted" role="status">{check}</span> : null}
        </label>
        <NumField label="Price you paid for this leg, $ (optional)" value={entry} onChange={setEntry} hint="Dollars per contract, e.g. 0.62." />
        <div class="row wrap">
          <button type="button" class="btn primary" disabled={problem !== null} onClick={add}>Add leg</button>
          {problem ? <span class="small muted">{problem}</span> : null}
        </div>
      </div>
    </details>
  );
}

/* ------------------------------------------------------------------ hedge calculator */
function HedgeCalc(props: { parlay: Parlay; rows: LegRow[]; paid: number | null }) {
  const open = props.rows.filter((r) => r.status.state !== "done" && r.status.state !== "dead");
  const [legId, setLegId] = useState("");
  const [manualNo, setManualNo] = useState<number | null>(null);
  const [count, setCount] = useState(0);
  const row = open.find((r) => r.leg.id === legId) ?? open[0];
  const shares = props.parlay.shares;
  if (open.length === 0) return <p class="small muted">Every leg is settled, so there is nothing left to hedge.</p>;
  if (!row) return null;
  const p = row.price;
  const yesMid = mid(p);
  const noAskFeed = p?.noAsk ?? (p?.yesBid != null ? 1 - p.yesBid : null);
  const noAsk = manualNo ?? noAskFeed;
  const noMidV = p?.noBid != null && p.noAsk != null ? (p.noBid + p.noAsk) / 2 : yesMid === null ? noAsk : 1 - yesMid;
  const ready = shares !== null && shares > 0 && props.paid !== null && noAsk !== null && noAsk > 0 && noAsk < 1;
  const n = shares !== null ? Math.min(count, Math.ceil(shares)) : 0;
  const h = ready && props.paid !== null ? hedge({ shares, paid: props.paid, noAsk, noMid: noMidV ?? noAsk, count: n }) : null;
  const others = open.length > 1;
  const avgChange = h && noAsk !== null && yesMid !== null ? n * ((1 - yesMid) - noAsk) - h.fee : null;
  return (
    <div class="stack tight tk-hedge">
      <h3>Hedge calculator</h3>
      <p class="small muted">
        Buying "No" on a leg pays $1 per contract if that leg fails. The full hedge is the number of No contracts that
        makes "the leg hits and everything else hits" pay the same as "the leg misses".
      </p>
      <label class="tk-field">
        <span class="label">Leg to hedge</span>
        <select value={row.leg.id} onChange={(e) => { setLegId(e.currentTarget.value); setManualNo(null); }}>
          {open.map((r) => (
            <option key={r.leg.id} value={r.leg.id}>{describeLeg(r.leg)}</option>
          ))}
        </select>
      </label>
      <p class="small num">
        No ask now: <b>{noAskFeed === null ? "no price" : cents(noAskFeed)}</b>
        {p ? ` (yes bid ${cents(p.yesBid)}, yes ask ${cents(p.yesAsk)})` : ""}
      </p>
      {noAskFeed === null ? (
        <NumField label="No ask, $ (type it in; no Kalshi price for this leg)" value={manualNo} onChange={setManualNo} hint="Dollars per No contract, e.g. 0.27." />
      ) : null}
      {!ready ? (
        <p class="muted small">Enter the parlay's contracts and total cost above (and a price for this leg) to size a hedge.</p>
      ) : h ? (
        <>
          <div class="row wrap">
            <button type="button" class="st-chip" onClick={() => { setCount(0); }}>No hedge</button>
            <button type="button" class="st-chip" onClick={() => { setCount(Math.ceil(fullHedgeCount(shares))); }}>
              Full hedge ({Math.ceil(fullHedgeCount(shares)).toString()} No)
            </button>
          </div>
          <label class="tk-field">
            <span class="label">
              Partial hedge: <b class="num">{n.toString()}</b> of {Math.ceil(shares).toString()} No contracts
            </span>
            <input
              type="range"
              min="0"
              max={Math.ceil(shares).toString()}
              step="1"
              value={n}
              onInput={(e) => { setCount(Number(e.currentTarget.value)); }}
            />
          </label>
          <table class="st-table num tk-out">
            <caption class="small muted">Net result after what you paid, the hedge cost and its fee</caption>
            <tbody>
              <tr><th scope="row">{others ? "This leg and all others hit" : "This leg hits"}</th><td>{money(h.netAllHit)}</td></tr>
              <tr><th scope="row">This leg misses</th><td>{money(h.netLegMisses)}</td></tr>
              {others ? (
                <tr><th scope="row">This leg hits, another leg misses</th><td>{money(h.netOtherMisses)}</td></tr>
              ) : null}
            </tbody>
          </table>
          <p class="small num">
            Hedge cost {money(h.cost)}, plus fee {money(h.fee)} (approximation: 7% x price x (1 - price) per contract, rounded up).
            Spread paid by buying at the ask instead of the mid: {money(h.spread)}.
          </p>
          {n === Math.ceil(shares) ? (
            <p class="small">
              Locked in: {money(h.netAllHit)} {others ? "if every other leg also hits; if another leg misses you lose the whole hedge cost on top of the parlay." : "whether this leg hits or not (to within one-contract rounding)."}
            </p>
          ) : null}
          {avgChange !== null ? (
            <p class="small">
              If the market's mid price is the true chance, this hedge changes your average result by{" "}
              <b class="num">{money(avgChange)}</b>. It does not add value; it moves value between outcomes and costs the
              spread and fee.
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ exit signal */
function ExitPanel(props: { parlay: Parlay; rows: LegRow[]; models: Record<string, LegModel | undefined>; onLeg: (id: string, pUser: number | null) => void; onOffer: (v: number | null) => void }) {
  const e = exitSignal(props.parlay, props.rows, props.models);
  const pc = (x: number | null): string => (x === null ? "–" : `${(x * 100).toFixed(1)}%`);
  return (
    <div class="stack tight tk-exit">
      <h3>Exit signal</h3>
      <p class="small muted">
        Compares what you could sell for now with what our pre-game model says the parlay is worth. There is no in-game
        model: once a game starts, a live leg uses the Kalshi mid unless you type your own number. Model probabilities
        are the pre-game ones and are unproven.
      </p>
      <ul class="tk-leglist">
        {e.legs.map((l) => (
          <li key={l.leg.id} class="tk-leg tk-leg-col">
            <b>{describeLeg(l.leg)}</b>
            <div class="small num">
              At entry <b>{pc(l.entryP)}</b> <span class="muted">({l.entrySource})</span>
            </div>
            <div class="small num">
              Now <b>{pc(l.nowP)}</b> <span class="muted">({l.nowSource})</span>
            </div>
            <NumField
              label="Your probability, % (optional)"
              value={l.leg.pUser == null ? null : Math.round(l.leg.pUser * 1000) / 10}
              onChange={(v) => { props.onLeg(l.leg.id, v === null ? null : Math.min(100, v) / 100); }}
            />
          </li>
        ))}
      </ul>
      <NumField label="Offer to exit, $ per contract (optional)" value={props.parlay.exitOffer ?? null} onChange={props.onOffer} hint="If empty, we use each leg sold at its Kalshi bid." />
      {e.missing.length > 0 ? (
        <p class="small muted">Not enough to compute everything: still needed, {e.missing.join("; ")}.</p>
      ) : null}
      <p class="small num">
        Entry edge: <b>{e.entryEdge === null ? "–" : money(e.entryEdge)}</b>
        {e.entryModelValue !== null ? ` (model value at entry ${money(e.entryModelValue)} minus ${money(paidOf(props.parlay) ?? 0)} paid)` : ""}.
        Model value now: <b>{e.modelNow === null ? "–" : money(e.modelNow)}</b>. Exit offer:{" "}
        <b>{e.offer === null ? "–" : money(e.offer)}</b> ({e.offerSource}).
        {e.spreadCost !== null ? ` Leaving costs ${money(e.spreadCost)} of spread against the mid.` : ""}
      </p>
      {e.marketOnly && (e.verdict === "gone" || e.verdict === "remains") ? (
        <p class="small muted">
          Every open leg is at the market's mid, so the offer sits below "model value now" by the spread alone. Type your
          own probability for a leg to get a real signal.
        </p>
      ) : null}
      <p class={`tk-signal v-${e.verdict}`} role="status">
        {e.verdict === "gone"
          ? "Edge gone: the offer is at or above the model's value. Taking it would lock in what the model thought the edge was."
          : e.verdict === "remains"
            ? "Edge remains: the offer is below the model's value, so by the model, holding is worth more than selling at that offer. The model is unproven."
            : e.verdict === "settled"
              ? "This parlay is settled; there is nothing to exit."
              : "No signal yet: the model has no entry probability for every leg (or you have not typed one), or a price is missing."}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ one parlay */
function ParlayCard(props: {
  parlay: Parlay;
  feed: Feed | null;
  models: Record<string, LegModel | undefined>;
  hist: History | null;
  scoreboard: LiveGameRow[];
  onChange: (p: Parlay) => void;
  onDelete: () => void;
}) {
  const p = props.parlay;
  const feed = props.feed;
  const sum = useMemo(
    () =>
      summarize(
        p,
        (l) => withMarket(legStatus(l, feed?.games[l.eventId] ?? null), feed?.markets[l.ticker]),
        (l) => priceOf(feed?.markets[l.ticker]),
      ),
    [p, feed],
  );
  const paid = paidOf(p);
  const comboPerShare = p.comboPrice ?? (p.totalCost !== null && p.shares !== null && p.shares > 0 ? p.totalCost / p.shares : null);
  const combo = comboPerShare === null ? null : comboCheck(comboPerShare, sum.rows);
  const set = (patch: Partial<Parlay>): void => { props.onChange({ ...p, ...patch }); };
  return (
    <article class="panel tk-parlay" aria-label={p.name}>
      <div class="row between wrap">
        <input class="tk-name" type="text" aria-label="Parlay name" value={p.name} onInput={(e) => { set({ name: e.currentTarget.value }); }} />
        <span class={`live-chip tk-${sum.state}`}>{sum.state === "won" ? "ALL HIT" : sum.state === "dead" ? "DEAD" : "OPEN"}</span>
      </div>

      <div class="tk-grid">
        <NumField label="Contracts" value={p.shares} onChange={(v) => { set({ shares: v }); }} step="1" hint="Each pays $1 if every leg hits." />
        <NumField label="Total paid, $" value={p.totalCost} onChange={(v) => { set({ totalCost: v }); }} />
        <NumField label="Combo price per contract, $" value={p.comboPrice} onChange={(v) => { set({ comboPrice: v }); }} hint="Optional; for the combo check." />
      </div>

      <ul class="tk-leglist">
        {sum.rows.map((r) => (
          <li key={r.leg.id} class="tk-leg">
            <div class="tk-leg-main">
              <b>{describeLeg(r.leg)}</b>
              <div class="small muted">{r.leg.gameLabel}</div>
              <div class="small num">{r.status.progress}</div>
              {r.status.note ? <div class="small muted">{r.status.note}</div> : null}
              {r.leg.ticker ? <div class="small muted tk-ticker">{r.leg.ticker}</div> : null}
            </div>
            <div class="tk-leg-side num">
              <span class={`live-chip tk-${r.status.state}`}>{STATE_TEXT[r.status.state]}</span>
              {r.price ? (
                <>
                  <b>{cents(mid(r.price))}</b>
                  <span class="small muted">{cents(r.price.yesBid)} / {cents(r.price.yesAsk)}</span>
                </>
              ) : r.leg.ticker ? (
                <span class="small muted">{feed === null ? "loading" : feed.kalshiError ? "no price" : "not found"}</span>
              ) : (
                <span class="small muted">no ticker</span>
              )}
              {r.leg.entry !== null ? <span class="small muted">paid {cents(r.leg.entry)}</span> : null}
              <button type="button" class="linkbtn" onClick={() => { set({ legs: p.legs.filter((l) => l.id !== r.leg.id) }); }}>
                Remove
              </button>
            </div>
          </li>
        ))}
      </ul>
      {p.legs.length === 0 ? <p class="muted small">No legs yet. Add one below.</p> : null}
      <AddLeg games={props.scoreboard} onAdd={(l) => { set({ legs: [...p.legs, l] }); }} />

      {p.legs.length > 0 ? (
        <section class="tk-sum stack tight" aria-label="Parlay value now">
          <h3>Value now</h3>
          {sum.state === "dead" ? (
            <p>
              <b>A leg is dead, so this parlay is worth $0.00.</b>
              {paid !== null ? ` You paid ${money(paid)}.` : ""}
            </p>
          ) : sum.fairValue === null ? (
            <p class="muted">
              {p.shares === null
                ? "Enter the number of contracts to see the value."
                : `Needs a price for: ${sum.unpriced.map((r) => describeLeg(r.leg)).join("; ")}. Add a Kalshi ticker to each open leg.`}
            </p>
          ) : (
            <>
              <p class="num">
                <span class="big-num">{money(sum.fairValue)}</span> fair value now
                {paid !== null && sum.gainNow !== null ? (
                  <>
                    {" "}
                    vs <b>{money(paid)}</b> paid ({sum.gainNow >= 0 ? "+" : ""}{money(sum.gainNow)} on paper)
                  </>
                ) : null}
              </p>
              <p class="small muted num">
                = {p.shares?.toString()} contracts x {((sum.jointP ?? 0) * 100).toFixed(1)}% (the product of each leg's mid price; hit legs
                count 100%). This assumes the legs are independent. Legs in the same game usually are not, so the product can
                be off in either direction.
              </p>
            </>
          )}
          {sum.riskLeg ? (
            <p class="small">
              Most remaining risk: <b>{describeLeg(sum.riskLeg.leg)}</b>, the lowest-priced open leg at{" "}
              <span class="num">{cents(sum.riskLeg.value)}</span>.
            </p>
          ) : null}
          <ExitPanel
            parlay={p}
            rows={sum.rows}
            models={props.models}
            onLeg={(id, v) => { set({ legs: p.legs.map((l) => (l.id === id ? { ...l, pUser: v } : l)) }); }}
            onOffer={(v) => { set({ exitOffer: v }); }}
          />
          <OptionsView parlay={p} rows={sum.rows} paid={paid} hist={props.hist} />
          <HedgeCalc parlay={p} rows={sum.rows} paid={paid} />
          {combo ? (
            <div class="stack tight">
              <h3>Combo vs legs</h3>
              <p class="small num">
                Combo price {cents(combo.combo)} per contract.{" "}
                {combo.productAsk !== null ? `Product of the legs' asks: ${cents(combo.productAsk)}. ` : "Product of the legs' asks: needs a price on every leg. "}
                {combo.productMid !== null ? `Product of mids: ${cents(combo.productMid)}.` : ""}
              </p>
              <p class="small">
                {combo.below && combo.gapVsAsk !== null
                  ? `The combo is ${cents(combo.gapVsAsk)} below the product of the asks. `
                  : "The combo is not below the product of the legs' asks. "}
                Only a combo priced below the product of its legs' asks would be an arbitrage-like gap, and Kalshi's spreads
                usually remove it. Even a gap would not be a sure thing: buying the legs one by one is not the same trade as
                the combo, fees apply to each, and legs in one game move together.
              </p>
            </div>
          ) : null}
        </section>
      ) : null}
      <button type="button" class="linkbtn danger" onClick={props.onDelete}>Delete this parlay</button>
    </article>
  );
}

/* ------------------------------------------------------------------ page */
function Strategy() {
  return (
    <section class="panel verdict" aria-label="Honest strategy">
      <h2>How to think about this</h2>
      <p>
        A parlay is like a digital option: it pays a fixed amount if everything lands and nothing if one thing doesn't.
        Cashing out or hedging a leg locks in a value, but it never adds expected value; at the quoted prices it costs a
        little, the spread and the fee.
      </p>
      <p>
        Spreading legs across different tip times gives you exit points: after the early games you know more and can
        hedge or hold. That is risk control, not profit. Long-shot parlays lose money on average unless some leg is
        priced wrong, and our own tests have not shown that we can find mispriced legs: the market beat us on game
        winners, and any edge on player props is small and unproven. See <a href="#/learned">What we learned</a>. Until
        the live log shows skill, the honest answer to "what should I bet?" is "keep your money."
      </p>
      <p>
        Exits do not add expected value when prices are fair, because a fair price is the expected value. Profit can only
        come from an entry edge: paying less than the true chance. Selling when the market catches up to the model locks
        that edge sooner and with less variance, but spreads are paid twice (once to get in, once to get out), and our
        edges are unproven.
      </p>
      <p class="small muted">
        This tracker reads public scores and public prices only. You type your legs in; it never logs into Kalshi, never
        sees your account and cannot place or change an order. Your list is saved in this browser only.
      </p>
    </section>
  );
}

export function TrackerPage({ importParam }: { importParam: string | null }) {
  const store = useMemo(() => browserStore(), []);
  const [parlays, setParlays] = useState<Parlay[]>(() => loadParlays(store));
  const [saveFailed, setSaveFailed] = useState(false);
  useEffect(() => {
    setSaveFailed(!saveParlays(store, parlays));
  }, [parlays, store]);

  const eventIds = [...new Set(parlays.flatMap((p) => p.legs.map((l) => l.eventId)).filter((id) => id !== ""))].sort();
  const tickers = [...new Set(parlays.flatMap((p) => p.legs.map((l) => l.ticker).filter((t) => t !== "")))].sort();
  const key = eventIds.join(",") + "|" + tickers.join(",");

  const feedPoll = usePoll<Feed>(async () => {
    const gameList = await Promise.all(eventIds.map((id) => api.liveGame(id).then((r) => r.data, () => null)));
    const games: Record<string, LiveGame | null> = {};
    eventIds.forEach((id, i) => { games[id] = gameList[i] ?? null; });
    const markets: Record<string, KalshiMarket> = {};
    let kalshiError: string | null = null;
    let stale = gameList.some((g) => g?.stale === true);
    if (tickers.length > 0) {
      try {
        const k = (await api.kalshi(tickers)).data;
        stale = stale || k.stale;
        for (const m of k.markets) markets[m.ticker] = m;
      } catch (e) {
        kalshiError = e instanceof Error ? e.message : String(e);
      }
    }
    return { games, markets, kalshiError, gamesFailed: gameList.filter((g) => g === null).length, stale };
  }, [key]);
  const [models, setModels] = useState<Record<string, LegModel | undefined>>({});
  const legs = parlays.flatMap((p) => p.legs);
  const modelKey = legs.map((l) => [l.id, l.kind, l.player, l.stat, l.line, l.side, l.team, l.startUtc].join("~")).join("|");
  useEffect(() => {
    let live = true;
    for (const l of legs) {
      if (models[l.id] !== undefined || l.kind === "spread" || l.kind === "total") continue;
      const date = new Date(l.startUtc).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
      const q = new URLSearchParams({ date, kind: l.kind, player: l.player, stat: l.stat, line: l.line.toString(), side: l.side, team: l.team });
      api.liveModel(q).then(
        (r) => { if (live) setModels((m) => ({ ...m, [l.id]: r.data })); },
        () => { if (live) setModels((m) => ({ ...m, [l.id]: { p: null, source: null, reason: "the model could not be reached" } })); },
      );
    }
    return () => { live = false; };
  }, [modelKey]);
  const histPoll = usePoll<History | null>(async () => (tickers.length === 0 ? null : (await api.liveHistory(tickers)).data), [key]);
  const [preview, setPreview] = useState<PreviewSource | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  useEffect(() => {
    if (importParam === null) return;
    const r = decodeImport(importParam);
    if (r.ok) {
      setImportErr(null);
      setPreview({ spec: r.spec, source: "From an import link" });
    } else setImportErr(`This import link could not be used: ${r.error}.`);
  }, [importParam]);
  const closeImport = (): void => {
    setPreview(null);
    if (window.location.hash.includes("import=")) window.history.replaceState(null, "", "#/tracker");
  };
  const scan = (f: File | undefined): void => {
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) {
      setScanMsg("That image is over 5 MB. Crop it or use a smaller screenshot.");
      return;
    }
    setScanning(true);
    setScanMsg(null);
    scanScreenshot(f).then(
      (r) => { setScanning(false); setPreview({ spec: r.parlay, source: r.source, screen: r.screen, resolved: r.resolved }); },
      (e: unknown) => { setScanning(false); setScanMsg(e instanceof Error ? e.message : "The scan failed."); },
    );
  };
  const sbPoll = usePoll(async () => (await api.liveScoreboard()).data, []);
  const feed = feedPoll.data;
  const board = sbPoll.data?.games ?? [];

  const update = (p: Parlay): void => { setParlays((all) => all.map((x) => (x.id === p.id ? p : x))); };
  const addParlay = (): void => {
    setParlays((all) => [...all, { id: uid(), name: `Parlay ${(all.length + 1).toString()}`, legs: [], shares: null, totalCost: null, comboPrice: null }]);
  };

  const overview = parlays
    .map((p) => {
      const s = summarize(
        p,
        (l) => withMarket(legStatus(l, feed?.games[l.eventId] ?? null), feed?.markets[l.ticker]),
        (l) => priceOf(feed?.markets[l.ticker]),
      );
      const openStarts = s.rows.filter((r) => r.status.state === "pre" || r.status.state === "live").map((r) => r.leg.startUtc).sort();
      return { p, s, next: openStarts[0] ?? null };
    })
    .sort((a, b) => (a.next ?? "9").localeCompare(b.next ?? "9"));

  return (
    <div class="stack">
      <div class="page-head">
        <h1>Parlay tracker</h1>
        <p class="muted">
          Follow parlays you already hold: live status of every leg, what the whole thing is worth now, and what a hedge
          would lock in. Analysis only, not advice.
        </p>
      </div>
      <Strategy />
      {importErr ? <p class="live-warn" role="alert">{importErr}</p> : null}
      {preview ? (
        <ImportPreview
          key={preview.source + preview.spec.name}
          src={preview}
          board={board}
          onAdd={(p) => { setParlays((all) => [...all, p]); closeImport(); }}
          onCancel={closeImport}
        />
      ) : null}
      <section class="panel" aria-label="Add from a screenshot">
        <h2>Add from a screenshot</h2>
        <p class="small muted">
          Pick a screenshot of one Kalshi parlay card. We read it and show you a form to check before anything is added.
        </p>
        <label class="btn" aria-busy={scanning}>
          {scanning ? "Reading the screenshot..." : "Scan screenshot"}
          <input class="sr" type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={scanning} onChange={(e) => { scan(e.currentTarget.files?.[0]); e.currentTarget.value = ""; }} />
        </label>
        {scanMsg ? <p class="live-warn" role="alert">{scanMsg}</p> : null}
        <p class="small muted">
          Privacy: when you use this, the image is sent to Anthropic to be read. It is not saved on our server and not
          logged. If scanning is not set up on this server, show the screenshot to Claude and open the import link it
          gives you instead.
        </p>
      </section>
      <SourceLine
        source="ESPN unofficial feed + Kalshi public prices"
        stale={feed?.stale ?? false}
        ageS={0}
        updatedAt={feedPoll.updatedAt}
        error={feedPoll.error}
        onRefresh={() => { feedPoll.reload(); sbPoll.reload(); }}
        loading={feedPoll.loading}
      />
      {feed && feed.kalshiError ? (
        <p class="live-warn" role="alert">Kalshi's public prices are not answering, so prices and values are blank. Leg status still comes from ESPN.</p>
      ) : null}
      {feed && feed.gamesFailed > 0 ? (
        <p class="live-warn" role="alert">ESPN did not return {feed.gamesFailed.toString()} of your games, so those legs show as unknown.</p>
      ) : null}
      {saveFailed ? (
        <p class="live-warn" role="status">This browser blocks storage, so your parlays will be gone when you close the page.</p>
      ) : null}

      {overview.length > 1 ? (
        <section class="panel" aria-label="Parlays by next tip">
          <h2>Spread across time</h2>
          <table class="st-table num">
            <thead>
              <tr><th scope="col">Parlay</th><th scope="col">Next open leg</th><th scope="col">Worth now</th></tr>
            </thead>
            <tbody>
              {overview.map(({ p, s, next }) => (
                <tr key={p.id}>
                  <th scope="row">{p.name}</th>
                  <td>{s.state === "dead" ? "dead" : s.state === "won" ? "all hit" : next ? new Date(next).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : "–"}</td>
                  <td>{s.fairValue === null ? "–" : money(s.fairValue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {parlays.length === 0 ? (
        <div class="panel empty">
          <h2>No parlays yet</h2>
          <p class="muted">Add one, then add its legs. You type in what you hold; nothing is read from any account.</p>
        </div>
      ) : null}
      {parlays.map((p) => (
        <ParlayCard
          key={p.id}
          parlay={p}
          feed={feed}
          models={models}
          hist={histPoll.data}
          scoreboard={board}
          onChange={update}
          onDelete={() => { setParlays((all) => all.filter((x) => x.id !== p.id)); }}
        />
      ))}
      <button type="button" class="btn primary" onClick={addParlay}>New parlay</button>
      <CsvImport
        parlays={parlays}
        onApply={(pid, lid, ticker, entry) => {
          setParlays((all) =>
            all.map((x) => (x.id !== pid ? x : { ...x, legs: x.legs.map((l) => (l.id === lid ? { ...l, ticker, entry: entry ?? l.entry } : l)) })),
          );
        }}
      />
      <p class="small muted">
        Scores: ESPN unofficial feed. Prices: Kalshi public prices. Both are fetched by our server, not your browser, and
        cached for about 20 seconds. A public deployment of either feed needs a licence check first.
      </p>
    </div>
  );
}
