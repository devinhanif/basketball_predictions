// Small pieces the betting screens share: money text, provenance and freshness, the money-safety note, the outcome table,
// the coverage block (or the model's reason it is not shown), one slip leg, and the save-state line. Every number here
// is a field of a quote, a leg or an evaluation from slipModel; this file only formats it.
import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { Badge, Button, Table, type Column } from "../ui";
import { MarketOnly } from "../Lens";
import { useLens } from "../prefs";
import { MINUS, MISSING, bookName, centsText, count, lineText, odds } from "../format";
import { agoText, stamp } from "../time";
import { STAT_LABEL } from "../types";
import { KNOWN_PROVIDERS, freshness } from "./catalog";
import { ARITHMETIC_NOTE, MONEY_NOTE, PROVENANCE_MEANING, PROVENANCE_WORD, REMOVE_RECORD_NOTE } from "./copy";
import { legCost, legPayout, legProblem, normalisedCost, type Coverage, type SlipEvaluation, type SlipRow } from "./slipModel";
import type { BetActions } from "./store";
import type { Market, ProviderInfo, Quote, Side, SlipLeg } from "./types";
import { typedQuote } from "./viewModel";
import { say } from "./useBet";

// ------------------------------------------------------------------------------------------------ text

/** Dollars to the cent: 24.5 -> "$24.50", -7 -> "−$7.00". Missing stays missing, never $0. */
export function money(x: number | null | undefined): string {
  if (typeof x !== "number" || !Number.isFinite(x)) return MISSING;
  const [whole = "0", cents = "00"] = Math.abs(x).toFixed(2).split(".");
  const body = `$${count(Number(whole))}.${cents}`;
  return x < 0 && Number(Math.abs(x).toFixed(2)) !== 0 ? `${MINUS}${body}` : body;
}

/** A net result with its sign: "+$7.00", "−$40.00", "$0.00". */
export function signedMoney(x: number | null | undefined): string {
  if (typeof x !== "number" || !Number.isFinite(x)) return MISSING;
  return Number(Math.abs(x).toFixed(2)) === 0 ? money(0) : x > 0 ? `+${money(x)}` : money(x);
}

export function providerLabel(id: string, registry?: readonly ProviderInfo[]): string {
  return registry?.find((p) => p.id === id)?.label ?? KNOWN_PROVIDERS.find((p) => p.id === id)?.label ?? bookName(id);
}

export const sideWord = (s: Side): string => (s === "yes" ? "over" : "under");

/** "Jalen Green over 3.5 assists". */
export function marketWords(m: Market, side: Side): string {
  return `${m.playerName} ${sideWord(side)} ${lineText(m.line)} ${STAT_LABEL[m.stat].toLowerCase()}`;
}

/** The native price in the venue's own unit, with the fee as known, typed or unknown. */
export function priceWords(q: Quote): string {
  const p = q.price;
  if (p.kind === "entry-pick") return "Entry pick: no price of its own (the entry's stake and multiplier price it)";
  const fee = p.fee.status === "unknown" ? "fee unknown" : `fee ${centsText(p.fee.centsPerDollarPayout, 2)} per $1 payout${p.fee.basis === "assumption" ? " (your entry)" : ""}`;
  return p.kind === "binary" ? `${centsText(p.askCents, 2)} per contract · ${fee}` : `${odds(p.americanOdds, "american")} · ${fee}`;
}

/** The fee part of a price, as known, typed or unknown. */
export function feeWords(q: Quote): string {
  const p = q.price;
  if (p.kind === "entry-pick") return "Priced by the whole entry";
  if (p.fee.status === "unknown") return "Fee unknown";
  return `Fee ${centsText(p.fee.centsPerDollarPayout, 2)} per $1 payout${p.fee.basis === "assumption" ? " (your entry)" : ""}`;
}

/** Cost per $1 of gross payout, or the model's reason there is none. */
export function costWords(q: Quote): string {
  const c = normalisedCost(q);
  return c.ok ? `${centsText(c.cents, 2)} per $1 of gross payout` : c.text;
}

// ------------------------------------------------------------------------------------------------ badges and notes

const PROV_TONE = { live: "model", delayed: "base", manual: "mine", sample: "none" } as const;

/** Provenance word, its meaning on hover, and when the price was seen (with its age). */
export function Provenance(props: { quote: Quote; now: number; label?: string }) {
  const q = props.quote;
  const f = freshness(q, props.now);
  const age = q.provenance === "sample" ? null : agoText(q.observedAt, new Date(props.now));
  return (
    <span class="ph-bet-prov">
      <Badge tone={PROV_TONE[q.provenance]} icon={q.provenance === "manual" ? "user" : q.provenance === "sample" ? "spark" : "history"} word={PROVENANCE_WORD[q.provenance]} title={PROVENANCE_MEANING[q.provenance]} />
      <span class="small muted num">
        {props.label ?? "seen"} {stamp(q.observedAt)}
        {age ? ` (${age})` : ""}
        {f === "stale" ? " · out of date" : f === "unknown" ? " · age unknown" : ""}
      </span>
    </span>
  );
}

/** The money-safety note; `arithmetic` adds the typed-prices sentence. */
export function MoneyNote(props: { arithmetic?: boolean }) {
  return (
    <p class="ph-bet-note" role="note">
      <strong>{MONEY_NOTE}</strong>
      {props.arithmetic ? <> {ARITHMETIC_NOTE}</> : null}
    </p>
  );
}

/** Betting screens are Market-lens tools: in Learn and Explore they show the app's "Market screen" note instead. */
export function MarketGate(props: { name: string; what: string; children: ComponentChildren }) {
  const lens = useLens();
  if (lens !== "market") return <MarketOnly name={props.name} what={props.what} />;
  return <>{props.children}</>;
}

// ------------------------------------------------------------------------------------------------ outcomes and coverage

/** Every listed outcome: payout and net with the whole slip, and net of the recorded positions alone (before). */
export function OutcomeTable(props: { evaluation: SlipEvaluation; caption?: string; limit?: number }) {
  const rows = props.evaluation.rows;
  if (!rows.length) return null;
  const shown = props.limit !== undefined ? rows.slice(0, props.limit) : rows;
  const hasExisting = rows.some((r) => r.existingNet !== null) && props.evaluation.groups.some((g) => g.legs.some((l) => l.role === "existing"));
  const cols: Column<SlipRow>[] = [
    { key: "o", head: "Outcome", cell: (r) => r.label },
    { key: "p", head: "Gross payout", num: true, cell: (r) => money(r.payout) },
    ...(hasExisting ? [{ key: "b", head: "Net, recorded only", num: true, cell: (r: SlipRow) => signedMoney(r.existingNet) }] : []),
    { key: "n", head: hasExisting ? "Net, with planned legs" : "Net", num: true, cell: (r) => <span class={r.net < 0 ? "ph-bet-neg" : r.net > 0 ? "ph-bet-pos" : ""}>{signedMoney(r.net)}</span> },
  ];
  return (
    <div class="ph-bet-outcomes">
      <Table caption={props.caption ?? "Every listed outcome at these prices"} rows={shown} columns={cols} rowKey={(r) => r.id} noun={shown.length === 1 ? "outcome" : "outcomes"} />
      {shown.length < rows.length ? <p class="small muted">Showing {count(shown.length)} of {count(rows.length)} outcomes; the full slip page lists them all.</p> : null}
    </div>
  );
}

/** The coverage summary exactly as the model gates it: shown with its basis, or the model's reason it is not. */
export function CoverageBlock(props: { coverage: Coverage }) {
  const c = props.coverage;
  if (!c.shown) {
    return (
      <div class="ph-bet-coverage off" role="note">
        <strong>Coverage summary not shown.</strong> {c.text}
      </div>
    );
  }
  return (
    <div class="ph-bet-coverage" role="note">
      <p>
        <strong>Across the listed outcomes, at these prices:</strong> worst net <span class="num">{signedMoney(c.worst)}</span>, best net{" "}
        <span class="num">{signedMoney(c.best)}</span>.
      </p>
      <p class="small">
        {c.allListedPositive ? "Every listed outcome nets above zero at these prices. " : "At least one listed outcome loses money at these prices. "}
        {c.improvesOnExisting === true ? "The planned legs raise the worst listed result compared with the recorded positions alone. " : c.improvesOnExisting === false ? "The planned legs do not raise the worst listed result compared with the recorded positions alone. " : ""}
        Listed outcomes are the over and under results of these markets only; did-not-play, voids and differing venue rules are not in them.
        {c.basis === "sample_prices" ? " These are invented sample prices." : ""}
      </p>
    </div>
  );
}

export function Warnings(props: { items: readonly string[] }) {
  if (!props.items.length) return null;
  return (
    <ul class="ph-bet-warn small">
      {props.items.map((w) => (
        <li key={w}>{w}</li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------------------------------------ typed price form

/** Native-unit price entry for one venue: cents for an exchange, American odds for a sportsbook, nothing for an entry. */
export function PriceForm(props: { provider: ProviderInfo; market: Market; side: Side; submitLabel: string; onQuote: (q: Quote) => void; idBase: string; initial?: { price: string; fee: string } }) {
  const [price, setPrice] = useState(props.initial?.price ?? "");
  const [fee, setFee] = useState(props.initial?.fee ?? "");
  const [err, setErr] = useState<string | null>(null);
  const kind = props.provider.kind;
  return (
    <form
      class="ph-bet-form"
      onSubmit={(e) => {
        e.preventDefault();
        const r = typedQuote(props.provider, props.market, props.side, { price, fee }, new Date().toISOString());
        if (!r.ok) {
          setErr(r.text);
          return;
        }
        setErr(null);
        props.onQuote(r.quote);
      }}
    >
      {kind === "entry" ? (
        <p class="small muted">An entry pick has no price of its own. Set the entry's stake and all-correct multiplier in the slip.</p>
      ) : (
        <div class="ph-bet-fields">
          <label>
            <span>{kind === "exchange" ? "Ask (cents per contract)" : "American odds"}</span>
            <input id={`${props.idBase}-price`} inputMode="decimal" autoComplete="off" value={price} placeholder={kind === "exchange" ? "e.g. 53" : "e.g. -110"} onInput={(e) => { setPrice(e.currentTarget.value); }} />
          </label>
          <label>
            <span>Fee (cents per $1 payout, blank if unknown)</span>
            <input id={`${props.idBase}-fee`} inputMode="decimal" autoComplete="off" value={fee} placeholder="unknown" onInput={(e) => { setFee(e.currentTarget.value); }} />
          </label>
        </div>
      )}
      {err ? (
        <p class="ph-bet-err small" role="alert">
          {err}
        </p>
      ) : null}
      <Button type="submit">{props.submitLabel}</Button>
    </form>
  );
}

// ------------------------------------------------------------------------------------------------ one leg

function AmountField(props: { leg: SlipLeg; actions: BetActions }) {
  const { leg } = props;
  if (leg.quote.price.kind === "entry-pick") return <span class="small muted">Part of the one entry below</span>;
  const exchange = leg.quote.price.kind === "binary";
  const label = `${exchange ? "Contracts" : "Stake ($)"} for ${marketWords(leg.market, leg.quote.side)} at ${providerLabel(leg.quote.provider)}`;
  if (leg.role === "existing") {
    return (
      <span class="num">
        {exchange ? `${count(leg.amount)} contracts` : `${money(leg.amount)} stake`} <span class="small muted">(recorded)</span>
      </span>
    );
  }
  return (
    <label class="ph-bet-amount">
      <span class="sr">{label}</span>
      <input
        key={String(leg.amount)}
        type="number"
        inputMode="decimal"
        min={exchange ? 1 : 0.01}
        step={exchange ? 1 : 0.01}
        defaultValue={leg.amount === null ? "" : String(leg.amount)}
        aria-label={label}
        onChange={(e) => {
          const v = Number(e.currentTarget.value);
          void props.actions.setAmount(leg.id, v).then((d) => say(d));
        }}
      />
      <span class="small muted">{exchange ? "contracts" : "stake"}</span>
    </label>
  );
}

export function LegCard(props: { leg: SlipLeg; actions: BetActions; now: number; compact?: boolean }) {
  const { leg, actions } = props;
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [editing, setEditing] = useState(false);
  const recorded = leg.role === "existing";
  const words = marketWords(leg.market, leg.quote.side);
  const venue = providerLabel(leg.quote.provider);
  const problem = legProblem(leg);
  const sample = leg.quote.provenance === "sample" || leg.market.origin === "sample";
  return (
    <article class={`ph-bet-leg${recorded ? " recorded" : ""}${!recorded && !leg.enabled ? " off" : ""}`}>
      <div class="ph-bet-leg-top">
        {recorded ? (
          <span class="ph-bet-lock" title="A recorded position always counts in exposure">
            <Badge tone="mine" icon="lock" word="Recorded" />
          </span>
        ) : (
          <label class="ph-bet-check">
            <input
              type="checkbox"
              checked={leg.enabled}
              aria-label={`Include ${words} at ${venue}`}
              onChange={() => {
                void actions.toggle(leg.id).then((d) => say(d));
              }}
            />
            <span>{leg.enabled ? "Included" : "Left out"}</span>
          </label>
        )}
        <strong class="ph-bet-venue">{venue}</strong>
        {sample ? <Badge tone="none" icon="spark" word="sample" title="Invented demo record" /> : null}
        <button
          type="button"
          class="icon-btn ph-bet-x"
          aria-label={`Remove ${words} at ${venue}`}
          onClick={() => {
            if (recorded) setConfirmRemove(true);
            else void actions.remove(leg.id).then((d) => say(d));
          }}
        >
          ×
        </button>
      </div>
      <p class="ph-bet-leg-what">
        {words}
        <span class="small muted"> · {leg.market.eventLabel || leg.market.eventId}</span>
      </p>
      <p class="small num">{priceWords(leg.quote)}</p>
      <Provenance quote={leg.quote} now={props.now} label={recorded ? "price when recorded" : "price when added"} />
      <div class="ph-bet-leg-nums">
        <AmountField leg={leg} actions={actions} />
        <span class="num small">
          Cost {money(legCost(leg))} · pays {money(legPayout(leg))} gross
        </span>
      </div>
      {problem ? <p class="small ph-bet-err">Not priced: {problem}.</p> : null}
      {!recorded && leg.quote.price.kind !== "entry-pick" && !props.compact ? (
        editing ? (
          <PriceForm
            provider={{ id: leg.quote.provider, label: venue, kind: leg.quote.price.kind === "binary" ? "exchange" : "sportsbook" }}
            market={leg.market}
            side={leg.quote.side}
            idBase={`edit-${leg.id}`}
            submitLabel="Use this price"
            onQuote={(q) => {
              void actions.replaceQuote(leg.id, q).then((d) => {
                if (say(d)) setEditing(false);
              });
            }}
          />
        ) : (
          <button type="button" class="linklike small" onClick={() => { setEditing(true); }}>
            Type a different price
          </button>
        )
      ) : null}
      {confirmRemove ? (
        <div class="ph-bet-confirm" role="group" aria-label="Confirm removing the record">
          <p class="small">{REMOVE_RECORD_NOTE}</p>
          <div class="row wrap">
            <Button onClick={() => { setConfirmRemove(false); }}>Keep it</Button>
            <Button
              onClick={() => {
                void actions.remove(leg.id).then((d) => say(d));
              }}
            >
              Remove the note
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}

// ------------------------------------------------------------------------------------------------ save state

/** The workspace's save state: an unsaved edit (retry / discard) or a store that could not be read. */
export function SaveState(props: { view: { phase: string; dirty: boolean; error: { code: string; text: string } | null }; onRetry: () => void; onDiscard: () => void; onReload: () => void }) {
  const { view } = props;
  if (!view.error) return null;
  if (view.dirty) {
    return (
      <div class="ph-bet-save panel error" role="alert">
        <p>
          <strong>Not saved.</strong> {view.error.text}
        </p>
        <p class="small">Your change is still on screen. Try again, or discard it to go back to what this browser holds.</p>
        <div class="row wrap">
          <Button kind="primary" onClick={props.onRetry}>
            Try saving again
          </Button>
          <Button onClick={props.onDiscard}>Discard the change</Button>
          {view.error.code === "conflict" ? <Button onClick={props.onReload}>Load the other tab's version</Button> : null}
        </div>
      </div>
    );
  }
  return (
    <div class="ph-bet-save panel error" role="alert">
      <p>
        <strong>The slip in this browser could not be used.</strong> {view.error.text}
      </p>
      <p class="small">Nothing was replaced. Try again after closing other tabs, or check the browser's storage settings.</p>
      <Button onClick={props.onReload}>Try again</Button>
    </div>
  );
}
