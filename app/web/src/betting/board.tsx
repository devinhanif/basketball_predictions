// What Compare and the Hedge Finder share: reading the stored snapshots (a typed 404 means the API's switch is off, and
// the screens then ask for prices by hand), resolving the market from the address, one venue card, and the
// "log a bet you already placed" form. Requests go only to this app's read-only API through api.ts.
import { Fragment, type ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, getJson } from "../api";
import { ApiError, failureMessage, type ApiFailure } from "../errors";
import { Avatar } from "../Avatar";
import { Badge, Button, Dialog } from "../ui";
import { centsText, count, lineText, longDate, odds } from "../format";
import { agoText, stamp } from "../time";
import { useLoad } from "../useLoad";
import { DEFAULT_MAX_QUOTE_AGE_MS, KNOWN_PROVIDERS, freshness } from "./catalog";
import { NO_SNAPSHOTS, PROVENANCE_MEANING, PROVENANCE_WORD, SETTLEMENT_NOTE } from "./copy";
import { PROV_TONE, PriceForm, marketWords, providerLabel } from "./parts";
import { sampleCatalog } from "./sample";
import { boardMatches, isStat, marketFromFacts, providerRegistry, storedBoard, type ApiBoard, type ApiProviders, type StoredBoard } from "./snapshots";
import { normalisedCost } from "./slipModel";
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

export function useProviders(skip: boolean): [Snap<ApiProviders>, () => void] {
  const [s, reload] = useLoad<Snap<ApiProviders>>(() => (skip ? offSnap<ApiProviders>() : betting<ApiProviders>("/api/betting/providers")), [skip]);
  return [unwrap(s), reload];
}

export interface Stored {
  providers: ProviderInfo[];
  stored: Catalog | null;
  board: StoredBoard | null;
  state: Snap<unknown>;
  source: "export" | "sample" | "manual";
  /** Reads the stored snapshots again (a new board; saved legs and ideas keep the prices they were saved at). */
  reload: () => void;
}

/** The whole stored board for one player and stat (every line and side), as the API serves it; never sample. */
export function useRawBoard(gameId: string, playerId: string, stat: string): [Snap<ApiBoard>, () => void] {
  const [b, reload] = useLoad<Snap<ApiBoard>>(() => betting<ApiBoard>(`/api/betting/board/${encodeURIComponent(gameId)}/${encodeURIComponent(playerId)}/${encodeURIComponent(stat)}`), [gameId, playerId, stat]);
  const snap = unwrap(b);
  // An earlier prop's answer still on screen for one render is loading, not this prop's board.
  if (snap.kind === "ok" && gameId !== "" && !boardMatches(snap.data, { eventId: gameId, playerId, stat })) return [{ kind: "loading" }, reload];
  return [snap, reload];
}

/** The stored board for an export market (none for typed markets; the demo's own invented board for sample markets). */
export function useStored(market: Market | null, side: Side): Stored {
  const source = market === null ? "manual" : market.origin === "sample" ? "sample" : market.origin === "manual" ? "manual" : "export";
  const [prov, reloadProv] = useProviders(source !== "export");
  const [b, reloadBoard] = useLoad<Snap<ApiBoard>>(
    () =>
      market === null || source !== "export"
        ? offSnap<ApiBoard>()
        : betting<ApiBoard>(`/api/betting/board/${encodeURIComponent(market.eventId)}/${encodeURIComponent(market.playerId)}/${encodeURIComponent(market.stat)}`),
    [market?.id ?? "", source],
  );
  const reload = (): void => {
    reloadProv();
    reloadBoard();
  };
  const board = unwrap(b);
  if (source === "sample") return { providers: [...KNOWN_PROVIDERS], stored: sampleCatalog(), board: null, state: { kind: "ok", data: null }, source, reload };
  if (source === "manual" || market === null) return { providers: [...KNOWN_PROVIDERS], stored: null, board: null, state: { kind: "off" }, source, reload };
  const providers = providerRegistry(prov.kind === "ok" ? prov.data : null);
  if (board.kind !== "ok") return { providers, stored: null, board: null, state: board, source, reload };
  // A slower, earlier answer for another prop must never read as this prop's board: until the matching answer arrives, loading.
  if (!boardMatches(board.data, market)) return { providers, stored: null, board: null, state: { kind: "loading" }, source, reload };
  const sb = storedBoard(board.data, market, side, providers);
  return { providers, stored: sb.catalog, board: sb, state: board, source, reload };
}

/** The newest and oldest stored snapshot times on the board shown (ISO), or null when nothing stored is shown. */
export function snapshotSpan(cards: readonly VenueCard[]): { newest: string; oldest: string } | null {
  const times = cards.map((c) => c.stored?.observedAt).filter((t): t is string => typeof t === "string" && t !== "");
  if (!times.length) return null;
  const sorted = [...times].sort();
  return { oldest: sorted[0] as string, newest: sorted[sorted.length - 1] as string };
}

/**
 * Where the prices come from and the caveats, said once above the venue list: stored snapshots are delayed (and when the
 * newest was taken), why a cost can be blank, that settlement rules are not verified as equivalent, and how venues are listed.
 */
export function AboutPrices(props: { st: Stored; cards: readonly VenueCard[]; now: number; crownText: string | null }) {
  const { st } = props;
  const span = snapshotSpan(props.cards);
  const stored = props.cards.flatMap((c) => (c.active && c.active.provenance === "delayed" ? [c.active] : []));
  const staleStored = stored.filter((q) => freshness(q, props.now) === "stale");
  const ageMinutes = Math.round(DEFAULT_MAX_QUOTE_AGE_MS / 60_000);
  const anyUnknownFee = props.cards.some((c) => c.active && c.active.price.kind !== "entry-pick" && c.active.price.fee.status === "unknown");
  let where: ComponentChildren;
  if (st.source === "sample") where = "Demo market: every price on it is an invented sample, labelled as such.";
  else if (st.source === "manual") where = "A market you typed: there are no stored prices for it. Type each venue's price.";
  else if (st.state.kind === "loading") where = "Reading stored snapshots...";
  else if (st.state.kind === "off") where = NO_SNAPSHOTS;
  else if (st.state.kind === "error")
    where = (
      <span class="ph-bet-err" role="alert">
        Stored snapshots could not be read: {failureMessage(st.state.failure)}. Nothing was filled in; type prices by hand or try again.
      </span>
    );
  else if (st.board && st.board.lines.length === 0) where = `No stored quotes for this player and stat. ${st.board.note} Type prices by hand.`;
  else if (st.board && !st.board.catalog.quotes.length)
    where = `Stored snapshots exist for this player and stat, but none on line ${lineText(st.board.catalog.markets[0]?.line ?? 0)} for this side. Lines stored: ${st.board.lines.map(lineText).join(", ")}.`;
  else
    where = (
      <>
        Stored snapshots from this app's nightly export: <strong>delayed, not live</strong>, the newest before tip-off for each venue
        {span ? (
          <>
            {" "}
            (taken <span class="num">{stamp(span.oldest)}</span>
            {span.oldest !== span.newest ? (
              <>
                {" "}
                to <span class="num">{stamp(span.newest)}</span>
              </>
            ) : null}
            {agoText(span.newest, new Date(props.now)) ? `, newest ${String(agoText(span.newest, new Date(props.now)))}` : ""})
          </>
        ) : null}
        .
      </>
    );
  return (
    <ul class="ph-bet-about small" aria-label="About these prices">
      <li>{where}</li>
      {staleStored.length ? (
        <li>
          A price older than {ageMinutes} minutes counts as out of date, so the slip shows no coverage figure while it is in a planned leg
          {staleStored.length === stored.length ? ": every stored snapshot on this board is older than that." : "; each row shows its age."} Type a current
          price to use one.
        </li>
      ) : null}
      {anyUnknownFee ? <li>Cost per $1 of payout is blank where a venue's fee is unknown. The export carries no fees; set one below if you know it.</li> : null}
      <li>{SETTLEMENT_NOTE}</li>
      <li>
        Venues are listed by name in two groups, priced then unavailable, never by price.
        {props.crownText ? ` ${props.crownText}` : ""}
      </li>
    </ul>
  );
}

// ------------------------------------------------------------------------------------------------ the market

export type MarketState = { kind: "none" } | { kind: "loading" } | { kind: "ready"; market: Market } | { kind: "error"; text: string; failure?: ApiFailure };

/** The market an address names: a typed one from the workspace, a demo one, or one from the export's game data. */
export function useMarketFromQuery(q: CompareQuery, ws: SlipWorkspace | null): MarketState {
  // The line may be left out (a prop picked from the night's list): the game's posted line for that stat is used.
  const exportish = q.market === null && q.game !== null && !q.game.startsWith("sample-") && q.player !== null && q.stat !== null;
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
  const posted = q.stat !== null && isStat(q.stat) ? p.props[q.stat]?.line : undefined;
  const line = q.line ?? posted ?? null;
  if (line === null) return { kind: "error", text: "There is no posted line for that prop in the game's data. Pick the prop from the game below and type the line." };
  const built = marketFromFacts({ gameId: game.game_id, date: game.date, playerId: pid, playerName: p.name, stat: q.stat ?? "", line, eventLabel: `${game.away.abbr} @ ${game.home.abbr}` });
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
const KIND_SHORT = { exchange: "Exchange", sportsbook: "Sportsbook", entry: "Entry (one stake, all-correct multiplier)" } as const;

export function nativePrice(q: Quote): string {
  const p = q.price;
  return p.kind === "binary" ? centsText(p.askCents, 2) : p.kind === "sportsbook" ? odds(p.americanOdds, "american") : "No per-pick price";
}

/** Provenance word and age on one short line (the full time is on hover and in the screen reader's text). */
export function SourceCell(props: { quote: Quote; now: number; /** The board says once above that old stored snapshots are out of date. */ quiet?: boolean }) {
  const q = props.quote;
  const f = freshness(q, props.now);
  const age = q.provenance === "sample" ? null : agoText(q.observedAt, new Date(props.now));
  const when = `${q.provenance === "manual" ? "typed" : "seen"} ${stamp(q.observedAt)}`;
  return (
    <span class="ph-bet-src" title={`${PROVENANCE_MEANING[q.provenance]} ${when}.`}>
      <Badge tone={PROV_TONE[q.provenance]} icon={q.provenance === "manual" ? "user" : q.provenance === "sample" ? "spark" : "history"} word={PROVENANCE_WORD[q.provenance]} />
      <span class="small muted num">
        {age ?? (q.provenance === "sample" ? "sample" : "age unknown")}
        {/* On a board, a stored (delayed) snapshot's staleness is said once above it; a typed price that went stale says it here. */}
        {f === "stale" && !(props.quiet && q.provenance === "delayed") ? " · out of date" : ""}
        <span class="sr">
          {" "}
          ({f === "stale" ? "out of date; " : ""}
          {when})
        </span>
      </span>
    </span>
  );
}

/** The fee as known, entered for this screen, or unknown. */
function feeCell(q: Quote, typed: boolean): string {
  const p = q.price;
  if (p.kind === "entry-pick") return "by the entry";
  if (p.fee.status === "unknown") return "unknown";
  return `${centsText(p.fee.centsPerDollarPayout, 2)}${typed || p.fee.basis === "assumption" ? " (yours)" : ""}`;
}

/** Cost per $1 of gross payout, or a short word for why there is none (the reason is said once above the list). */
function costCell(q: Quote): string {
  const c = normalisedCost(q);
  if (c.ok) return centsText(c.cents, 2);
  return c.code === "entry_only" ? "priced as an entry" : c.code === "fee_unknown" ? "needs a fee" : "not valid";
}

export interface BoardRowExtra {
  /** A line under the row (the Hedge Finder's size), and an optional disclosure body. */
  line?: ComponentChildren;
  more?: { summary: string; body: () => ComponentChildren };
}

/**
 * The venues for one market and side as a compact board: priced venues, then unavailable ones (still listed by name),
 * each group in name sequence and never by price. One row per venue: name and kind, native price, provenance and age, fee,
 * cost per $1 of payout when it can be shown, and one quiet action. On a phone each row is a two-line card.
 */
export function VenueBoard(props: {
  cards: readonly VenueCard[];
  market: Market;
  side: Side;
  now: number;
  fees: Readonly<Record<string, string>>;
  crowned: string | null;
  unusable: (provider: string) => readonly string[];
  addLabel: (venue: string) => string;
  addWord?: string;
  onAdd: (q: Quote, card: VenueCard) => void;
  onType: (provider: ProviderInfo) => void;
  extra?: (card: VenueCard, quote: Quote) => BoardRowExtra | null;
  caption: string;
}) {
  const priced = props.cards.filter((c) => c.active !== null);
  const missing = props.cards.filter((c) => c.active === null);
  const quoteOf = (c: VenueCard): Quote | null => {
    const fee = props.fees[c.provider.id] ?? "";
    if (!c.active || fee.trim() === "") return c.active;
    const r = withTypedFee(c.active, fee);
    return r.ok ? r.quote : c.active;
  };
  const row = (c: VenueCard) => {
    const q = quoteOf(c);
    const venue = c.provider.label;
    const extra = q && props.extra ? props.extra(c, q) : null;
    return (
      <Fragment key={c.provider.id}>
      <tr class={`${q ? "priced" : "unavailable"}${extra ? " has-extra" : ""}`} data-venue={c.provider.id}>
        <th scope="row" class="ph-bet-b-venue">
          <span class="ph-bet-b-name">{venue}</span>
          <span class="small muted ph-bet-b-kind" title={KIND_WORDS[c.provider.kind]}>
            {KIND_SHORT[c.provider.kind]}
          </span>
          {c.typed && c.stored ? (
            <span class="small muted ph-bet-b-sub num">
              stored {nativePrice(c.stored)} ({stamp(c.stored.observedAt)}); your typed price is used
            </span>
          ) : null}
          {c.inSlip.map((l) => (
            <span key={l.id} class="small ph-bet-b-sub num">
              In your slip at {nativePrice(l.quote)} (price when added, {stamp(l.quote.observedAt)})
              {c.stored && c.stored.id !== l.quote.id ? `; newest stored ${nativePrice(c.stored)}` : ""}
            </span>
          ))}
          {props.crowned === c.provider.id ? <span class="small ph-bet-crown">Lowest cost per $1 among comparable venues</span> : null}
        </th>
        {q ? (
          <>
            <td class="ph-bet-b-price num" data-label="Price">
              {nativePrice(q)}
            </td>
            <td class="ph-bet-b-src" data-label="Source">
              <SourceCell quote={q} now={props.now} quiet />
            </td>
            <td class="ph-bet-b-fee num small" data-label="Fee">
              <span class="ph-bet-b-lab">Fee </span>
              {feeCell(q, (props.fees[c.provider.id] ?? "").trim() !== "")}
            </td>
            <td class={`ph-bet-b-cost num small${normalisedCost(q).ok ? "" : " nocost"}`} data-label="Cost per $1">
              <span class="ph-bet-b-lab">Cost per $1 </span>
              {costCell(q)}
            </td>
          </>
        ) : (
          <td class="ph-bet-b-none small" colSpan={4}>
            No stored price for this side{props.unusable(c.provider.id).length ? ` (stored quote unusable: ${props.unusable(c.provider.id).join("; ")})` : ""}.
          </td>
        )}
        <td class="ph-bet-b-act">
          {q ? (
            <button type="button" class="btn small" aria-label={props.addLabel(venue)} onClick={() => { props.onAdd(q, c); }}>
              {props.addWord ?? "Add"}
            </button>
          ) : null}
          <button type="button" class={q ? "linklike small" : "btn small quiet"} aria-label={`Type a ${venue} price`} onClick={() => { props.onType(c.provider); }}>
            {q ? "Type" : c.provider.kind === "entry" ? "Use as entry pick" : "Type a price"}
          </button>
        </td>
      </tr>
      {extra ? (
        <tr class="ph-bet-b-extra">
          <td colSpan={6} class="small">
            {extra.line}
            {extra.more ? (
              <details class="ph-bet-b-more">
                <summary>{extra.more.summary}</summary>
                <Lazy render={extra.more.body} />
              </details>
            ) : null}
          </td>
        </tr>
      ) : null}
      </Fragment>
    );
  };
  return (
    <div class="ph-bet-board-wrap">
      <table class="ph-bet-board">
        <caption class="sr">{props.caption}</caption>
        <thead>
          <tr>
            <th scope="col">Venue</th>
            <th scope="col" class="num">
              Price
            </th>
            <th scope="col">Source</th>
            <th scope="col" class="num">
              Fee
            </th>
            <th scope="col" class="num">
              Cost per $1
            </th>
            <th scope="col">
              <span class="sr">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr class="ph-bet-b-group">
            <th scope="rowgroup" colSpan={6}>
              Priced <span class="muted num">({count(priced.length)})</span>
            </th>
          </tr>
          {priced.length ? priced.map(row) : (
            <tr>
              <td colSpan={6} class="small muted">
                No venue has a price for this side yet. Type one from a row below.
              </td>
            </tr>
          )}
        </tbody>
        {missing.length ? (
          <tbody>
            <tr class="ph-bet-b-group">
              <th scope="rowgroup" colSpan={6}>
                Unavailable <span class="muted num">({count(missing.length)})</span>
              </th>
            </tr>
            {missing.map(row)}
          </tbody>
        ) : null}
      </table>
    </div>
  );
}

/** Renders a disclosure's body only once it has been opened (a hedge's before/after tables are not built for every row). */
function Lazy(props: { render: () => ComponentChildren }) {
  const [shown, setShown] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const d = ref.current?.closest("details");
    if (!d) return;
    const on = (): void => {
      if (d.open) setShown(true);
    };
    d.addEventListener("toggle", on);
    return () => {
      d.removeEventListener("toggle", on);
    };
  }, []);
  return <span ref={ref}>{shown ? props.render() : null}</span>;
}

/** The one "set a fee" control above the board: a venue whose fee is unknown, and your fee for it (this screen only). */
export function FeeControl(props: { cards: readonly VenueCard[]; fees: Readonly<Record<string, string>>; onFees: (f: Record<string, string>) => void }) {
  const unknown = props.cards.filter((c) => c.active && c.active.price.kind !== "entry-pick" && c.active.price.fee.status === "unknown");
  const set = Object.entries(props.fees).filter(([, v]) => v.trim() !== "");
  const [venue, setVenue] = useState("");
  const [fee, setFee] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!unknown.length && !set.length) return null;
  const chosen = venue || unknown[0]?.provider.id || "";
  const label = (id: string): string => props.cards.find((c) => c.provider.id === id)?.provider.label ?? id;
  return (
    <div class="ph-bet-feebar">
      {unknown.length ? (
        <form
          class="ph-bet-feeform"
          onSubmit={(e) => {
            e.preventDefault();
            const c = props.cards.find((x) => x.provider.id === chosen);
            const r = c?.active ? withTypedFee(c.active, fee) : null;
            if (!r || !r.ok || fee.trim() === "") {
              setErr(r && !r.ok ? r.text : "Type a fee from 0 to 100 cents per $1 of payout.");
              return;
            }
            setErr(null);
            props.onFees({ ...props.fees, [chosen]: fee.trim() });
            setFee("");
          }}
        >
          <label>
            <span>Set a fee for</span>
            <select value={chosen} onChange={(e) => { setVenue(e.currentTarget.value); }}>
              {unknown.map((c) => (
                <option key={c.provider.id} value={c.provider.id}>
                  {c.provider.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Cents per $1 of payout</span>
            <input inputMode="decimal" autoComplete="off" value={fee} placeholder="e.g. 1" onInput={(e) => { setFee(e.currentTarget.value); }} />
          </label>
          <Button type="submit">Use this fee</Button>
        </form>
      ) : null}
      {err ? (
        <p class="small ph-bet-err" role="alert">
          {err}
        </p>
      ) : null}
      {set.length ? (
        <p class="small">
          Your fees on this screen (your entry, not a verified fee):{" "}
          {set.map(([id, v], i) => (
            <span key={id}>
              {i ? ", " : ""}
              {label(id)} {centsText(Number(v), 2)}{" "}
              <button
                type="button"
                class="linklike small"
                aria-label={`Clear the fee for ${label(id)}`}
                onClick={() => {
                  props.onFees(Object.fromEntries(Object.entries(props.fees).filter(([k]) => k !== id)));
                }}
              >
                clear
              </button>
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}

/** The typed-price form for one venue, in the shared Dialog. */
export function TypePriceDialog(props: { provider: ProviderInfo; market: Market; side: Side; actions: BetActions; onClose: () => void }) {
  const venue = props.provider.label;
  return (
    <Dialog title={props.provider.kind === "entry" ? `Use ${venue} as an entry pick` : `Type a ${venue} price`} onClose={props.onClose}>
      <div class="ph-bet">
        <p class="small muted">
          {marketWords(props.market, props.side)}. A price you type is kept as your entry (manual), labelled as such; it is not a verified quote.
        </p>
        <PriceForm
          provider={props.provider}
          market={props.market}
          side={props.side}
          idBase={`dlg-${props.provider.id}`}
          submitLabel={props.provider.kind === "entry" ? `Use ${venue} as an entry pick` : `Use this ${venue} price`}
          onQuote={(q) => {
            void props.actions.putTyped(q).then((d) => {
              if (!say(d)) return;
              props.onClose();
              // The row moved from "Unavailable" to "Priced": put focus on its first action, not on a button that is gone.
              window.setTimeout(() => {
                document.querySelector<HTMLElement>(`tr[data-venue="${props.provider.id}"] .ph-bet-b-act button`)?.focus();
              }, 50);
            });
          }}
        />
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------------------ an existing bet

/** "Log a bet you already placed": a local record of a position held elsewhere, at the price on the viewer's ticket. */
export function ExistingBetForm(props: { market: Market; side: Side; providers: readonly ProviderInfo[]; actions: BetActions; onDone?: () => void }) {
  const venues = props.providers.filter((p) => p.kind !== "entry");
  const [provider, setProvider] = useState(venues.find((p) => p.id === "draftkings")?.id ?? venues[0]?.id ?? "draftkings");
  const [side, setSide] = useState<Side>(props.side);
  const [amount, setAmount] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const info = venues.find((p) => p.id === provider) ?? { id: provider, label: providerLabel(provider), kind: "sportsbook" as const };
  return (
    <div class="ph-bet-existing">
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
              props.onDone?.();
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
        <Button type="submit" kind="primary">
          Record this position
        </Button>
      </form>
    </div>
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

