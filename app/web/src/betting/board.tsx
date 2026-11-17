// What Compare and the Hedge Finder share: reading the stored snapshots (a typed 404 means the API's switch is off, and
// the screens then ask for prices by hand), resolving the market from the address, one venue card, and the
// "log a bet you already placed" form. Requests go only to this app's read-only API through api.ts.
import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { api, getJson } from "../api";
import { ApiError, failureMessage, type ApiFailure } from "../errors";
import { Avatar } from "../Avatar";
import { Button } from "../ui";
import { centsText, lineText, longDate, odds } from "../format";
import { stamp } from "../time";
import { useLoad } from "../useLoad";
import { KNOWN_PROVIDERS } from "./catalog";
import { NO_SNAPSHOTS } from "./copy";
import { PriceForm, Provenance, costWords, feeWords, marketWords, providerLabel } from "./parts";
import { sampleCatalog } from "./sample";
import { marketFromFacts, providerRegistry, storedBoard, type ApiBoard, type ApiProviders, type StoredBoard } from "./snapshots";
import type { BetActions } from "./store";
import type { Catalog, Market, ProviderInfo, Quote, Side, SlipWorkspace } from "./types";
import { knownMarkets, typedQuote, withTypedFee, type CompareQuery, type VenueCard } from "./viewModel";
import { say } from "./useBet";

export type Snap<T> = { kind: "loading" } | { kind: "ok"; data: T } | { kind: "off" } | { kind: "error"; failure: ApiFailure };

/** A betting route: its answer, "off" for the typed 404 (switch off, or the snapshot page), or the failure. Never sample. */
async function betting<T>(path: string): Promise<{ data: Snap<T>; fromCache: boolean }> {
  try {
    const r = await getJson<T>(path);
    return { data: { kind: "ok", data: r.data }, fromCache: r.fromCache };
  } catch (e) {
    if (e instanceof ApiError && e.failure.status === 404) return { data: { kind: "off" }, fromCache: false };
    throw e;
  }
}

const unwrap = <T,>(s: { status: "loading" } | { status: "error"; failure: ApiFailure } | { status: "ready"; data: Snap<T> }): Snap<T> =>
  s.status === "loading" ? { kind: "loading" } : s.status === "error" ? { kind: "error", failure: s.failure } : s.data;

function offSnap<T>(): Promise<{ data: Snap<T>; fromCache: boolean }> {
  return Promise.resolve({ data: { kind: "off" }, fromCache: false });
}

export function useProviders(skip: boolean): Snap<ApiProviders> {
  const [s] = useLoad<Snap<ApiProviders>>(() => (skip ? offSnap<ApiProviders>() : betting<ApiProviders>("/api/betting/providers")), [skip]);
  return unwrap(s);
}

/** The stored board for an export market (none for typed markets; the demo's own invented board for sample markets). */
export function useStored(market: Market | null, side: Side): { providers: ProviderInfo[]; stored: Catalog | null; board: StoredBoard | null; state: Snap<unknown>; source: "export" | "sample" | "manual" } {
  const source = market === null ? "manual" : market.origin === "sample" ? "sample" : market.origin === "manual" ? "manual" : "export";
  const prov = useProviders(source !== "export");
  const [b] = useLoad<Snap<ApiBoard>>(
    () =>
      market === null || source !== "export"
        ? offSnap<ApiBoard>()
        : betting<ApiBoard>(`/api/betting/board/${encodeURIComponent(market.eventId)}/${encodeURIComponent(market.playerId)}/${encodeURIComponent(market.stat)}`),
    [market?.id ?? "", source],
  );
  const board = unwrap(b);
  if (source === "sample") return { providers: [...KNOWN_PROVIDERS], stored: sampleCatalog(), board: null, state: { kind: "ok", data: null }, source };
  if (source === "manual" || market === null) return { providers: [...KNOWN_PROVIDERS], stored: null, board: null, state: { kind: "off" }, source };
  const providers = providerRegistry(prov.kind === "ok" ? prov.data : null);
  if (board.kind !== "ok") return { providers, stored: null, board: null, state: board, source };
  const sb = storedBoard(board.data, market, side, providers);
  return { providers, stored: sb.catalog, board: sb, state: board, source };
}

/** One line on where the prices come from, in words. */
export function SnapshotStatus(props: { state: Snap<unknown>; source: "export" | "sample" | "manual"; board: StoredBoard | null }) {
  const { state, source } = props;
  if (source === "sample") return <p class="small">Demo market: every price on it is an invented sample, labelled as such.</p>;
  if (source === "manual") return <p class="small">A market you typed: there are no stored prices for it. Type each venue's price below.</p>;
  if (state.kind === "loading") return <p class="small muted">Reading stored snapshots...</p>;
  if (state.kind === "off") return <p class="small">{NO_SNAPSHOTS}</p>;
  if (state.kind === "error")
    return (
      <p class="small ph-bet-err" role="alert">
        Stored snapshots could not be read: {failureMessage(state.failure)}. Nothing was filled in; type prices by hand or try again later.
      </p>
    );
  const b = props.board;
  if (b && b.lines.length === 0) return <p class="small">No stored quotes for this player and stat. {b.note} Type prices by hand below.</p>;
  return (
    <p class="small">
      Stored snapshots from this app's nightly export: delayed, not live, the newest before tip-off for each venue.
      {b && !b.lines.length ? "" : b && !b.catalog.quotes.length ? ` None on line ${lineText(b.catalog.markets[0]?.line ?? 0)} for this side; lines stored: ${b.lines.map(lineText).join(", ")}.` : ""}
    </p>
  );
}

// ------------------------------------------------------------------------------------------------ the market

export type MarketState = { kind: "none" } | { kind: "loading" } | { kind: "ready"; market: Market } | { kind: "error"; text: string; failure?: ApiFailure };

/** The market an address names: a typed one from the workspace, a demo one, or one from the export's game data. */
export function useMarketFromQuery(q: CompareQuery, ws: SlipWorkspace | null): MarketState {
  const exportish = q.market === null && q.game !== null && !q.game.startsWith("sample-") && q.player !== null && q.stat !== null && q.line !== null;
  const [g] = useLoad(() => (exportish && q.game ? api.game(q.game) : Promise.resolve({ data: null, fromCache: false })), [exportish ? q.game : ""]);
  if (q.market !== null) {
    const m = ws ? knownMarkets(ws).find((x) => x.id === q.market) : undefined;
    return m ? { kind: "ready", market: m } : ws ? { kind: "error", text: "That typed market is not in this browser's slip. It may have been removed, or the link came from another browser." } : { kind: "loading" };
  }
  if (q.game?.startsWith("sample-")) {
    const m = sampleCatalog().markets.find((x) => x.eventId === q.game && x.playerId === q.player && x.stat === q.stat && x.line === q.line);
    return m ? { kind: "ready", market: m } : { kind: "error", text: "That demo market does not exist." };
  }
  if (!exportish) return { kind: "none" };
  if (g.status === "loading") return { kind: "loading" };
  if (g.status === "error") return { kind: "error", text: g.message, failure: g.failure };
  const game = g.data;
  const pid = Number(q.player);
  const p = game?.players.find((x) => x.player_id === pid);
  if (!game || !p) return { kind: "error", text: "That player is not in this game's data." };
  const built = marketFromFacts({ gameId: game.game_id, date: game.date, playerId: pid, playerName: p.name, stat: q.stat ?? "", line: q.line ?? Number.NaN, eventLabel: `${game.away.abbr} @ ${game.home.abbr}` });
  return built.ok ? { kind: "ready", market: built.market } : { kind: "error", text: built.text };
}

/** The market's heading: picture (the app's Avatar), who, what, which game. */
export function MarketHead(props: { market: Market; side: Side; children?: ComponentChildren }) {
  const m = props.market;
  const pid = m.origin === "export" && /^\d+$/.test(m.playerId) ? Number(m.playerId) : null;
  return (
    <div class="ph-bet-market">
      <Avatar name={m.playerName} playerId={pid} size="lg" remote={pid !== null} />
      <div>
        <h2>{marketWords(m, props.side)}</h2>
        <p class="small muted">
          {m.eventLabel || m.eventId} · {longDate(m.eventDate)}
          {m.origin === "sample" ? " · sample market" : m.origin === "manual" ? " · a market you typed" : ""}
        </p>
        {props.children}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ one venue

const KIND_WORDS = { exchange: "Exchange · whole contracts, $1 each if right", sportsbook: "Sportsbook · stake at American odds", entry: "Entry · one stake, one all-correct multiplier" } as const;

function nativePrice(q: Quote): string {
  const p = q.price;
  return p.kind === "binary" ? centsText(p.askCents, 2) : p.kind === "sportsbook" ? odds(p.americanOdds, "american") : "No per-pick price";
}

/** A venue card. `extra` is the hedge size (Hedge Finder) and `onAdd` adds the active quote to the slip. */
export function VenueCardView(props: {
  card: VenueCard;
  market: Market;
  side: Side;
  now: number;
  actions: BetActions;
  crowned: boolean;
  unusable: readonly string[];
  onAdd: (q: Quote) => void;
  addLabel: string;
  extra?: ComponentChildren;
}) {
  const { card, market, side } = props;
  const [fee, setFee] = useState("");
  const [typing, setTyping] = useState(false);
  const [feeErr, setFeeErr] = useState<string | null>(null);
  const venue = card.provider.label;
  const withFee = card.active && fee.trim() !== "" ? withTypedFee(card.active, fee) : null;
  const quote = withFee?.ok ? withFee.quote : card.active;
  const needsFee = quote !== null && quote.price.kind !== "entry-pick" && quote.price.fee.status === "unknown";
  const id = `v-${card.provider.id}`;
  return (
    <section class={`ph-bet-card${quote ? "" : " unavailable"}`} aria-label={`${venue}, ${marketWords(market, side)}`}>
      <span class="ph-bet-kind">{KIND_WORDS[card.provider.kind]}</span>
      <h3>{venue}</h3>
      {quote ? (
        <>
          <p class="ph-bet-price num">{nativePrice(quote)}</p>
          <p class="small num">{feeWords(quote)}</p>
          <Provenance quote={quote} now={props.now} />
          <p class="small num">
            <strong>{costWords(quote)}</strong>
          </p>
          {props.crowned ? <p class="ph-bet-crown small">Lowest cost per $1 of payout among comparable venues.</p> : null}
          {card.typed && card.stored ? <p class="small muted">Stored price {nativePrice(card.stored)}, seen {stamp(card.stored.observedAt)}; your typed price is used.</p> : null}
          {needsFee ? (
            <details class="ph-bet-fee" open={fee !== "" ? true : undefined}>
              <summary>Fee unknown: enter your own</summary>
              <label>
                <span>Your fee entry (cents per $1 payout)</span>
                <input
                  id={`${id}-fee`}
                  inputMode="decimal"
                  value={fee}
                  placeholder="unknown"
                  onInput={(e) => {
                    const v = e.currentTarget.value;
                    setFee(v);
                    const r = card.active ? withTypedFee(card.active, v) : null;
                    setFeeErr(r && !r.ok ? r.text : null);
                  }}
                />
              </label>
            </details>
          ) : quote.price.kind !== "entry-pick" && quote.price.fee.status === "known" && quote.price.fee.basis === "assumption" && fee !== "" ? (
            <p class="small">Fee: your entry.</p>
          ) : null}
          {feeErr ? <p class="small ph-bet-err">{feeErr}</p> : null}
        </>
      ) : (
        <p class="small">Unavailable: no stored price for this side{props.unusable.length ? ` (stored quote unusable: ${props.unusable.join("; ")})` : ""}.</p>
      )}
      {card.inSlip.map((l) => (
        <p key={l.id} class="small">
          In your slip at {nativePrice(l.quote)} (price when added, {stamp(l.quote.observedAt)})
          {card.stored && card.stored.id !== l.quote.id ? `; newest stored ${nativePrice(card.stored)}` : ""}.
        </p>
      ))}
      {props.extra}
      {typing ? (
        <PriceForm
          provider={card.provider}
          market={market}
          side={side}
          idBase={id}
          submitLabel={card.provider.kind === "entry" ? `Use ${venue} as an entry pick` : `Use this ${venue} price`}
          onQuote={(q) => {
            void props.actions.putTyped(q).then((d) => {
              if (say(d)) setTyping(false);
            });
          }}
        />
      ) : (
        <button type="button" class={quote ? "linklike small" : "btn"} onClick={() => { setTyping(true); }}>
          {quote ? `Type a different ${venue} price` : card.provider.kind === "entry" ? `Use ${venue} as an entry pick` : `Type a ${venue} price`}
        </button>
      )}
      {quote ? (
        <Button
          kind="primary"
          onClick={() => {
            props.onAdd(quote);
          }}
        >
          {props.addLabel}
        </Button>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ an existing bet

/** "Log a bet you already placed": a local record of a position held elsewhere, at the price on the viewer's ticket. */
export function ExistingBetForm(props: { market: Market; side: Side; providers: readonly ProviderInfo[]; actions: BetActions }) {
  const venues = props.providers.filter((p) => p.kind !== "entry");
  const [provider, setProvider] = useState(venues.find((p) => p.id === "draftkings")?.id ?? venues[0]?.id ?? "draftkings");
  const [side, setSide] = useState<Side>(props.side);
  const [amount, setAmount] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const info = venues.find((p) => p.id === provider) ?? { id: provider, label: providerLabel(provider), kind: "sportsbook" as const };
  return (
    <details class="ph-bet-existing">
      <summary>Log a bet you already placed on this market</summary>
      <form
        class="ph-bet-form"
        onSubmit={(e) => {
          e.preventDefault();
          const q = typedQuote(info, props.market, side, { price, fee }, new Date().toISOString());
          if (!q.ok) {
            setErr(q.text);
            return;
          }
          const n = Number(amount);
          void props.actions.addRecorded(q.quote, props.market, n).then((d) => {
            if (say(d)) {
              setErr(null);
              setAmount("");
              setPrice("");
            } else setErr(d.text);
          });
        }}
      >
        <p class="small muted">Use the price and amount from your own ticket. This writes a note in this browser only.</p>
        <div class="ph-bet-fields">
          <label>
            <span>Venue</span>
            <select value={provider} onChange={(e) => { setProvider(e.currentTarget.value); }}>
              {venues.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Side</span>
            <select value={side} onChange={(e) => { setSide(e.currentTarget.value === "no" ? "no" : "yes"); }}>
              <option value="yes">Over (YES)</option>
              <option value="no">Under (NO)</option>
            </select>
          </label>
          <label>
            <span>{info.kind === "exchange" ? "Contracts" : "Stake ($)"}</span>
            <input inputMode="decimal" value={amount} onInput={(e) => { setAmount(e.currentTarget.value); }} />
          </label>
          <label>
            <span>{info.kind === "exchange" ? "Price paid (cents)" : "American odds"}</span>
            <input inputMode="decimal" value={price} onInput={(e) => { setPrice(e.currentTarget.value); }} />
          </label>
          <label>
            <span>Fee (cents per $1 payout, blank if unknown)</span>
            <input inputMode="decimal" value={fee} placeholder="unknown" onInput={(e) => { setFee(e.currentTarget.value); }} />
          </label>
        </div>
        {err ? (
          <p class="small ph-bet-err" role="alert">
            {err}
          </p>
        ) : null}
        <Button type="submit">Record this position</Button>
      </form>
    </details>
  );
}

/** The failure screen for a market that could not be read. */
export function MarketProblem(props: { text: string; failure?: ApiFailure }) {
  return (
    <div class="panel error" role="alert">
      <h2>That market could not be opened</h2>
      <p>{props.failure ? failureMessage(props.failure) : props.text}</p>
      <p>
        <a class="btn" href="#/bet/compare">
          Pick another prop
        </a>
      </p>
    </div>
  );
}

