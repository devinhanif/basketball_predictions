// #/bet/compare?game=..&player=..&stat=..&line=..&side=.. (or ?market=<typed market id>&side=..): one prop's venues side
// by side. Each card is the venue's stored snapshot (delayed) or "unavailable" with a field to type a price; native
// price, cost per $1 of gross payout from slipModel (or the model's reason it is blocked), provenance and age. Venues are
// listed by name; a "lowest cost" mark appears only when the catalog says the comparison is like-for-like. The state is
// in the address, so a comparison can be shared and Back works.
import { useState } from "preact/hooks";
import { api, getJson } from "../api";
import { ApiError, failureMessage } from "../errors";
import { Button, Dialog, PageHead, Skeleton } from "../ui";
import { count, lineText, longDate, tipTime } from "../format";
import { todayET } from "../time";
import { useLoad, type LoadState } from "../useLoad";
import { STATS, STAT_LABEL, type GameDetail, type Stat } from "../types";
import type { BetRoute } from "./seam";
import { ARITHMETIC_NOTE, NO_SNAPSHOTS, TITLE_COMPARE } from "./copy";
import { evaluateDraft } from "./slipModel";
import { customMarket, isStat, sideOf, type ApiSlate } from "./snapshots";
import { compareHashFor, knownMarkets, lowestCostCrown, parseCompareQuery, venueCards } from "./viewModel";
import { CoverageBlock, MarketGate, MoneyNote, OutcomeTable, marketWords, money } from "./parts";
import { AboutPrices, ExistingBetForm, FeeControl, MarketHead, MarketProblem, TypePriceDialog, VenueBoard, useMarketFromQuery, useStored } from "./board";
import { ensureBetStyles, revealSlip, say, useBet, useNow, type BetHook } from "./useBet";
import type { Market, ProviderInfo, Side } from "./types";

type Ready = Extract<BetHook, { ready: true }>;

const go = (hash: string): void => {
  window.location.hash = hash;
};

// ------------------------------------------------------------------------------------------------ choosing a prop

function pickNight(dates: readonly string[]): string | null {
  const today = todayET();
  const sorted = [...dates].sort();
  if (sorted.includes(today)) return today;
  const past = sorted.filter((d) => d <= today);
  return past[past.length - 1] ?? sorted[sorted.length - 1] ?? null;
}

function GamePicker(props: { gameId: string; stored: ApiSlate | null; onPick: (m: { game: GameDetail; playerId: number; stat: Stat; line: number; side: "over" | "under" }) => void }) {
  const [g, reload] = useLoad(() => api.game(props.gameId), [props.gameId]);
  const [player, setPlayer] = useState<number | null>(null);
  const [stat, setStat] = useState<Stat | "">("");
  const [line, setLine] = useState("");
  const [side, setSide] = useState<"over" | "under">("over");
  if (g.status === "loading") return <Skeleton lines={2} label="Loading the game" />;
  if (g.status === "error")
    return (
      <p class="ph-bet-err" role="alert">
        The game could not be read: {g.message}{" "}
        <button type="button" class="linklike" onClick={reload}>
          Try again
        </button>
      </p>
    );
  const game = g.data;
  const p = game.players.find((x) => x.player_id === player) ?? null;
  const stats = p ? STATS.filter((s) => p.props[s] !== undefined) : [];
  const storedFor = (pid: number, s?: string): number => props.stored?.props.filter((x) => x.game_id === game.game_id && x.player_id === pid && (s === undefined || x.stat === s)).reduce((n, x) => Math.max(n, x.n_providers), 0) ?? 0;
  const players = [...game.players].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div class="ph-bet-picker">
      <label>
        <span>Player</span>
        <select
          value={player === null ? "" : String(player)}
          onChange={(e) => {
            const id = Number(e.currentTarget.value);
            setPlayer(Number.isFinite(id) && e.currentTarget.value !== "" ? id : null);
            setStat("");
            setLine("");
          }}
        >
          <option value="">Choose a player</option>
          {players.map((x) => {
            const n = storedFor(x.player_id);
            return (
              <option key={x.player_id} value={String(x.player_id)}>
                {x.name} ({x.side === "home" ? game.home.abbr : game.away.abbr}){n ? ` · stored prices` : ""}
              </option>
            );
          })}
        </select>
      </label>
      <label>
        <span>Stat</span>
        <select
          value={stat}
          disabled={!p}
          onChange={(e) => {
            const s = e.currentTarget.value as Stat | "";
            setStat(s);
            const pv = s && p ? p.props[s] : undefined;
            setLine(pv ? String(pv.line) : "");
          }}
        >
          <option value="">Choose a stat</option>
          {stats.map((s) => (
            <option key={s} value={s}>
              {STAT_LABEL[s]}
              {p && storedFor(p.player_id, s) ? ` · ${String(storedFor(p.player_id, s))} venues stored` : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Line</span>
        <input inputMode="decimal" value={line} disabled={!stat} onInput={(e) => { setLine(e.currentTarget.value); }} />
      </label>
      <label>
        <span>Side</span>
        <select value={side} onChange={(e) => { setSide(e.currentTarget.value === "under" ? "under" : "over"); }}>
          <option value="over">Over</option>
          <option value="under">Under</option>
        </select>
      </label>
      <Button
        kind="primary"
        disabled={!p || !stat || !/^\d{1,4}(\.\d{1,2})?$/.test(line.trim())}
        onClick={() => {
          if (p && stat) props.onPick({ game, playerId: p.player_id, stat, line: Number(line.trim()), side });
        }}
      >
        Compare venues
      </Button>
    </div>
  );
}

function CustomMarketForm(props: { bet: Ready }) {
  const [f, setF] = useState({ playerName: "", eventLabel: "", date: todayET(), stat: "pts", line: "", side: "over" as "over" | "under" });
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { currentTarget: { value: string } }): void => {
    setF({ ...f, [k]: e.currentTarget.value });
  };
  return (
    <details class="ph-bet-custom">
      <summary>Type your own market (a player or game this app does not list)</summary>
      <form
        class="ph-bet-form"
        onSubmit={(e) => {
          e.preventDefault();
          const r = customMarket({ playerName: f.playerName, eventLabel: f.eventLabel, date: f.date, stat: f.stat, line: f.line.trim() === "" ? Number.NaN : Number(f.line) });
          if (!r.ok) {
            setErr(r.text);
            return;
          }
          const side = sideOf(f.side);
          void props.bet.actions.addPick(r.market, side, "custom").then((d) => {
            if (say(d)) go(compareHashFor(r.market, side));
            else setErr(d.text);
          });
        }}
      >
        <div class="ph-bet-fields">
          <label>
            <span>Player</span>
            <input value={f.playerName} onInput={set("playerName")} autoComplete="off" />
          </label>
          <label>
            <span>Game (e.g. BOS @ NYK)</span>
            <input value={f.eventLabel} onInput={set("eventLabel")} autoComplete="off" />
          </label>
          <label>
            <span>Date (YYYY-MM-DD)</span>
            <input value={f.date} onInput={set("date")} />
          </label>
          <label>
            <span>Stat</span>
            <select value={f.stat} onChange={set("stat")}>
              {STATS.map((s) => (
                <option key={s} value={s}>
                  {STAT_LABEL[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Line</span>
            <input inputMode="decimal" value={f.line} onInput={set("line")} />
          </label>
          <label>
            <span>Side</span>
            <select value={f.side} onChange={set("side")}>
              <option value="over">Over</option>
              <option value="under">Under</option>
            </select>
          </label>
        </div>
        {err ? (
          <p class="small ph-bet-err" role="alert">
            {err}
          </p>
        ) : null}
        <Button type="submit">Add to slip and compare</Button>
      </form>
    </details>
  );
}

const SHOW = 24;

/** The night's props that have stored quotes (from the read-only betting slate), searchable, each a link into Compare. */
function StoredProps(props: { night: string; state: LoadState<ApiSlate | null> }) {
  const [query, setQuery] = useState("");
  const [more, setMore] = useState(false);
  const st = props.state;
  if (st.status === "loading") return <Skeleton lines={2} label="Reading the night's stored props" />;
  if (st.status === "error")
    return (
      <p class="small ph-bet-err" role="alert">
        The night's stored props could not be read: {failureMessage(st.failure)}. Pick a prop from a game below, or type your own market.
      </p>
    );
  if (st.data === null) return <p class="small">{NO_SNAPSHOTS} Pick a prop from a game below, or type your own market.</p>;
  const slate = st.data;
  const games = new Map(slate.games.map((g) => [g.game_id, `${g.away} @ ${g.home}`]));
  const words = query.toLowerCase().split(/\s+/).filter((w) => w !== "");
  const match = slate.props.filter((p) => {
    const hay = `${p.player} ${p.team} ${games.get(p.game_id) ?? ""} ${isStat(p.stat) ? STAT_LABEL[p.stat] : p.stat}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  const shown = more ? match : match.slice(0, SHOW);
  return (
    <section aria-labelledby="ph-bet-stored-h">
      <h2 class="ph-bet-h" id="ph-bet-stored-h">
        Props with stored prices, {longDate(slate.date)}
      </h2>
      <p class="small muted num">
        {count(slate.props.length)} props across {count(slate.games.length)} games have at least one stored quote. Listed by game, then player; nothing is ranked.
      </p>
      <label class="ph-bet-search">
        <span class="sr">Search the night's props</span>
        <input type="search" placeholder="Search a player, team or stat" value={query} onInput={(e) => { setQuery(e.currentTarget.value); setMore(false); }} />
      </label>
      {match.length ? (
        <ul class="plain ph-bet-proplist">
          {shown.map((p) => (
            <li key={`${p.game_id}-${String(p.player_id)}-${p.stat}`}>
              <a href={`#/bet/compare?game=${encodeURIComponent(p.game_id)}&player=${String(p.player_id)}&stat=${encodeURIComponent(p.stat)}&side=over`}>
                <strong>{p.player}</strong> <span class="muted">({p.team})</span> {isStat(p.stat) ? STAT_LABEL[p.stat].toLowerCase() : p.stat}
              </a>
              <span class="small muted num">
                {games.get(p.game_id) ?? p.game_id} · {count(p.n_providers)} {p.n_providers === 1 ? "venue" : "venues"} · {count(p.n_lines)} {p.n_lines === 1 ? "line" : "lines"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p class="small">No prop matches that search.</p>
      )}
      {!more && match.length > SHOW ? (
        <Button onClick={() => { setMore(true); }}>
          Show all {count(match.length)}
        </Button>
      ) : null}
    </section>
  );
}

function Picker(props: { bet: Ready }) {
  const [list, reloadList] = useLoad(() => api.slates(), []);
  const [date, setDate] = useState<string | null>(null);
  const [gameId, setGameId] = useState("");
  const night = date ?? (list.status === "ready" ? pickNight(list.data.map((s) => s.date)) : null);
  const [slate] = useLoad(() => (night ? api.slate(night) : Promise.resolve({ data: null, fromCache: false })), [night ?? ""]);
  const [bs] = useLoad(
    () =>
      night
        ? getJson<ApiSlate>(`/api/betting/slate/${encodeURIComponent(night)}`).catch((e: unknown) => {
            // A typed 404 means this server has no stored snapshots (its switch is off); anything else is an error.
            if (e instanceof ApiError && e.failure.status === 404) return { data: null, fromCache: false };
            throw e;
          })
        : Promise.resolve({ data: null, fromCache: false }),
    [night ?? ""],
  );
  const ws = props.bet.view.workspace;
  const held = ws ? knownMarkets(ws).slice(0, 8) : [];
  return (
    <div class="stack">
      {held.length ? (
        <section aria-label="From your slip">
          <h2 class="ph-bet-h">From your slip</h2>
          <ul class="plain">
            {held.map((m) => (
              <li key={m.id}>
                <a href={compareHashFor(m, ws?.picks.find((p) => p.market.id === m.id)?.side ?? "yes")}>{marketWords(m, ws?.picks.find((p) => p.market.id === m.id)?.side ?? "yes")}</a>
                <span class="small muted"> · {m.eventLabel}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {night ? <StoredProps night={night} state={bs} /> : null}
      <section aria-label="Pick a prop">
        <h2 class="ph-bet-h">Or pick any prop from a night's games</h2>
        {list.status === "loading" ? <Skeleton lines={2} label="Loading the nights" /> : null}
        {list.status === "error" ? (
          <p class="ph-bet-err" role="alert">
            The list of nights could not be read: {list.message}{" "}
            <button type="button" class="linklike" onClick={reloadList}>
              Try again
            </button>
          </p>
        ) : null}
        {list.status === "ready" ? (
          <div class="ph-bet-picker">
            <label>
              <span>Night</span>
              <select value={night ?? ""} onChange={(e) => { setDate(e.currentTarget.value); setGameId(""); }}>
                {[...list.data].sort((a, b) => b.date.localeCompare(a.date)).map((s) => (
                  <option key={s.date} value={s.date}>
                    {longDate(s.date)} ({s.games} games)
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Game</span>
              <select value={gameId} disabled={slate.status !== "ready" || !slate.data} onChange={(e) => { setGameId(e.currentTarget.value); }}>
                <option value="">{slate.status === "loading" ? "Loading..." : "Choose a game"}</option>
                {slate.status === "ready" && slate.data
                  ? slate.data.games.map((g) => (
                      <option key={g.game_id} value={g.game_id}>
                        {g.away.abbr} @ {g.home.abbr} · {tipTime(g.tip_utc)}
                      </option>
                    ))
                  : null}
              </select>
            </label>
          </div>
        ) : null}
        {slate.status === "error" ? <p class="ph-bet-err small" role="alert">That night's games could not be read: {slate.message}</p> : null}
        {gameId && night ? (
          <GamePicker
            key={gameId}
            gameId={gameId}
            stored={bs.status === "ready" ? bs.data : null}
            onPick={(m) => {
              go(`#/bet/compare?game=${encodeURIComponent(m.game.game_id)}&player=${String(m.playerId)}&stat=${m.stat}&line=${lineText(m.line)}&side=${m.side}`);
            }}
          />
        ) : null}
      </section>
      <CustomMarketForm bet={props.bet} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ the comparison

export function Comparison(props: { bet: Ready; market: Market; side: Side }) {
  const { bet, market, side } = props;
  const now = useNow();
  const st = useStored(market, side);
  const [fees, setFees] = useState<Record<string, string>>({});
  const [dialog, setDialog] = useState<null | { kind: "type"; provider: ProviderInfo } | { kind: "existing" }>(null);
  const ws = bet.view.workspace;
  if (!ws) return null;
  const cards = venueCards({ providers: st.providers, stored: st.stored, workspace: ws, market, side, now });
  const crown = lowestCostCrown(cards, market, now);
  const ev = evaluateDraft(ws.draft, { now });
  const lines = st.board?.lines ?? [];
  const unusable = (provider: string): string[] => st.board?.unusable.filter((u) => u.provider === provider).map((u) => u.reason) ?? [];
  return (
    <>
      <MarketHead market={market} side={side}>
        <div class="ph-bet-headrow">
          <div class="ph-bet-seg" role="group" aria-label="Side">
            {(["yes", "no"] as const).map((s) => (
              <a key={s} class="btn small" href={compareHashFor(market, s)} aria-pressed={s === side ? "true" : "false"} aria-current={s === side ? "true" : undefined}>
                {s === "yes" ? "Over" : "Under"} {lineText(market.line)}
              </a>
            ))}
          </div>
          {lines.length > 1 && market.origin === "export" ? (
            <label class="ph-bet-linepick">
              <span>Line ({lines.length} stored)</span>
              <select
                value={String(market.line)}
                onChange={(e) => {
                  go(compareHashFor({ ...market, line: Number(e.currentTarget.value) }, side));
                }}
              >
                {(lines.includes(market.line) ? lines : [market.line, ...lines]).map((l) => (
                  <option key={l} value={String(l)}>
                    {lineText(l)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
      </MarketHead>
      <div class="ph-bet-toolbar row wrap">
        {st.source === "export" ? (
          <Button
            onClick={() => {
              st.reload();
              say({ ok: true, text: "Stored prices read again. Legs in your slip and saved ideas keep the price they were saved at." }, "Prices");
            }}
          >
            Refresh stored prices
          </Button>
        ) : null}
        <Button onClick={() => { setDialog({ kind: "existing" }); }}>Log a bet you already placed</Button>
        {market.origin === "export" ? (
          <a class="btn" href={compareHashFor(market, side, "analysis")}>
            Model and record for this prop
          </a>
        ) : null}
      </div>
      <AboutPrices st={st} cards={cards} now={now} crownText={crown.shown ? null : `No venue is marked cheapest: ${crown.reasons[0] ?? "the comparison is not like-for-like."}`} />
      <FeeControl cards={cards} fees={fees} onFees={setFees} />
      <VenueBoard
        cards={cards}
        market={market}
        side={side}
        now={now}
        fees={fees}
        crowned={crown.shown ? crown.provider : null}
        unusable={unusable}
        caption={`Venues for ${marketWords(market, side)}: priced first, then unavailable, each by name`}
        addLabel={(venue) => `Add ${venue} to slip`}
        onAdd={(q) => {
          void bet.actions.addQuote(q, market).then((d) => {
            if (say(d)) revealSlip();
          });
        }}
        onType={(provider) => {
          setDialog({ kind: "type", provider });
        }}
      />
      <section aria-labelledby="ph-bet-cmp-now">
        <h2 class="ph-bet-h" id="ph-bet-cmp-now">
          Your slip now
        </h2>
        {ws.draft.legs.length ? (
          <details class="ph-bet-disclose">
            <summary>
              Cost {money(ev.totalCost)} · {ev.rows.length ? `${String(ev.rows.length)} listed outcomes` : "outcomes not listed yet"}: show before and after the planned legs
            </summary>
            <p class="small muted">{ARITHMETIC_NOTE} "Recorded only" is the positions you already hold; "with planned legs" adds every included planned leg.</p>
            <CoverageBlock coverage={ev.coverage} />
            <OutcomeTable evaluation={ev} />
            {!ev.rows.length ? <p class="small">No outcome table yet: {ev.warnings[0] ?? "a leg is incomplete."}</p> : null}
          </details>
        ) : (
          <p class="small">Nothing in the slip yet. Add a venue above, or log a bet you already placed.</p>
        )}
      </section>
      {dialog?.kind === "type" ? <TypePriceDialog provider={dialog.provider} market={market} side={side} actions={bet.actions} onClose={() => { setDialog(null); }} /> : null}
      {dialog?.kind === "existing" ? (
        <Dialog title="Log a bet you already placed" onClose={() => { setDialog(null); }}>
          <div class="ph-bet">
            <p class="small">{marketWords(market, side)}</p>
            <ExistingBetForm market={market} side={side} providers={st.providers} actions={bet.actions} onDone={() => { setDialog(null); }} />
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

export function CompareScreen(props: { route: BetRoute }) {
  const bet = useBet();
  const q = parseCompareQuery(props.route.query);
  const ms = useMarketFromQuery(q, bet.ready ? bet.view.workspace : null);
  return (
    <section class="ph-bet stack" aria-labelledby="ph-bet-cmp-h">
      <PageHead
        id="ph-bet-cmp-h"
        kicker="Betting tools"
        title={TITLE_COMPARE}
        description="One prop's price at each venue, side by side, with where each price came from, how old it is and its fee."
      />
      <MoneyNote arithmetic />
      {!bet.ready || bet.view.workspace === null ? (
        <Skeleton lines={3} label="Loading your slip" />
      ) : ms.kind === "none" ? (
        <Picker bet={bet} />
      ) : ms.kind === "loading" ? (
        <Skeleton lines={3} label="Loading the market" />
      ) : ms.kind === "error" ? (
        <MarketProblem text={ms.text} failure={ms.failure} />
      ) : (
        <>
          <p class="small">
            <a class="tap" href="#/bet/compare">Pick another prop</a>
          </p>
          <Comparison bet={bet} market={ms.market} side={sideOf(q.side)} />
        </>
      )}
    </section>
  );
}

export function ComparePage(props: { route: BetRoute }) {
  ensureBetStyles();
  return (
    <MarketGate name="Compare" what="Compare puts venues' prices side by side.">
      <CompareScreen route={props.route} />
    </MarketGate>
  );
}
