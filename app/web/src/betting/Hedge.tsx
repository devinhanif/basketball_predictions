// #/bet/hedge?leg=<id>: Compare with intent "hedge". It starts from the exact positions recorded in the slip (ids,
// prices, sides, amounts), lets the viewer pick any held position, takes the opposite side of that market, and sizes a
// hedge at each venue against the position's uncovered gross payout (hedgeMath via slipModel: whole contracts rounded up
// at an exchange, stake to the cent at a sportsbook). Before/after outcome tables come from evaluateDraft.
import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { PageHead, Skeleton } from "../ui";
import { count } from "../format";
import type { BetRoute } from "./seam";
import { ARITHMETIC_NOTE, TITLE_HEDGE } from "./copy";
import { legCost, legPayout, makeHedgeLeg } from "./slipModel";
import type { Market, ProviderInfo, Quote, Side, SlipLeg } from "./types";
import { hedgeSideFor, heldPositions, parseCompareQuery, previewWithLeg, venueCards } from "./viewModel";
import { CoverageBlock, MarketGate, MoneyNote, OutcomeTable, marketWords, money, providerLabel } from "./parts";
import { AboutPrices, FeeControl, MarketHead, TypePriceDialog, VenueBoard, useStored } from "./board";
import { ensureBetStyles, revealSlip, say, useBet, useNow, type BetHook } from "./useBet";

type Ready = Extract<BetHook, { ready: true }>;

function HeldChips(props: { held: readonly SlipLeg[]; selected: string }) {
  return (
    <nav class="ph-bet-held" aria-label="Held positions">
      {props.held.map((l) => (
        <a key={l.id} class="ph-bet-chip" href={`#/bet/hedge?leg=${encodeURIComponent(l.id)}`} aria-current={l.id === props.selected ? "true" : undefined}>
          <strong>{marketWords(l.market, l.quote.side)}</strong>
          <span class="small num">
            {providerLabel(l.quote.provider)} · {l.quote.price.kind === "binary" ? `${count(l.amount)} contracts` : `${money(l.amount)} stake`} · cost {money(legCost(l))}
            {l.quote.provenance === "sample" ? " · sample" : ""}
          </span>
        </a>
      ))}
    </nav>
  );
}

function Preview(props: { bet: Ready; market: Market; quote: Quote; amount: number | null | undefined; now: number }) {
  const ws = props.bet.view.workspace;
  if (!ws) return null;
  const p = previewWithLeg(ws.draft, props.quote, props.market, props.amount, props.now);
  return (
    <div class="ph-bet-preview">
      <p class="small muted">{ARITHMETIC_NOTE} "Before" is your slip as it is (recorded positions and included planned legs); "after" adds this hedge.</p>
      {p.text ? <p class="small ph-bet-err">{p.text}</p> : null}
      <div class="ph-bet-split">
        <div>
          <OutcomeTable evaluation={p.before} caption="Before: your slip now" />
          {!p.before.rows.length ? <p class="small">No table: {p.before.warnings[0] ?? "a leg is incomplete."}</p> : null}
        </div>
        <div>
          {p.after ? (
            <>
              <OutcomeTable evaluation={p.after} caption="After: with this hedge" />
              {!p.after.rows.length ? <p class="small">No table: {p.after.warnings[0] ?? "a leg is incomplete."}</p> : null}
              <CoverageBlock coverage={p.after.coverage} />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** The hedge size at one venue, in words: whole contracts or a stake, what it pays gross, and its cost when the fee is known. */
function sizeLine(r: ReturnType<typeof makeHedgeLeg>, sideWord: string): ComponentChildren {
  if (!r.ok) return <span class="ph-bet-err">{r.text}</span>;
  if (!r.sized) return <span>{r.text}</span>;
  const l = r.leg;
  const amount = l.quote.price.kind === "binary" ? `${count(l.amount)} contracts` : `a ${money(l.amount)} stake`;
  const cost = legCost(l);
  return (
    <span class="num">
      <strong>Hedge size: {amount}</strong> · pays {money(legPayout(l))} gross if {sideWord} · cost {cost === null ? "needs a fee" : money(cost)}
    </span>
  );
}

function Finder(props: { bet: Ready; leg: SlipLeg; held: readonly SlipLeg[] }) {
  const { bet, leg } = props;
  const now = useNow();
  const market = leg.market;
  const side: Side = hedgeSideFor(leg);
  const st = useStored(market, side);
  const [fees, setFees] = useState<Record<string, string>>({});
  const [typing, setTyping] = useState<ProviderInfo | null>(null);
  const ws = bet.view.workspace;
  if (!ws) return null;
  const cards = venueCards({ providers: st.providers, stored: st.stored, workspace: ws, market, side, now });
  const unusable = (provider: string): string[] => st.board?.unusable.filter((u) => u.provider === provider).map((u) => u.reason) ?? [];
  const word = side === "yes" ? "over" : "under";
  return (
    <>
      <HeldChips held={props.held} selected={leg.id} />
      <MarketHead market={market} side={side}>
        <p class="small">
          Hedging your {providerLabel(leg.quote.provider)} {leg.quote.side === "yes" ? "over" : "under"} position: the other side is shown at every venue, sized to cover the position's gross payout.
        </p>
      </MarketHead>
      <AboutPrices st={st} cards={cards} now={now} crownText={null} />
      <FeeControl cards={cards} fees={fees} onFees={setFees} />
      <VenueBoard
        cards={cards}
        market={market}
        side={side}
        now={now}
        fees={fees}
        crowned={null}
        unusable={unusable}
        caption={`Hedge venues for ${marketWords(market, side)}: priced first, then unavailable, each by name`}
        addLabel={(venue) => `Add ${venue} hedge to slip`}
        addWord="Add hedge"
        extra={(_c, q) => {
          const r = makeHedgeLeg(ws.draft, q, market);
          const amount = r.ok ? r.leg.amount : undefined;
          return {
            line: sizeLine(r, word),
            more: { summary: "Show before and after", body: () => <Preview bet={bet} market={market} quote={q} amount={amount} now={now} /> },
          };
        }}
        onAdd={(q) => {
          const sized = makeHedgeLeg(ws.draft, q, market);
          const amount = sized.ok ? sized.leg.amount : undefined;
          void bet.actions.addQuote(q, market, amount).then((d) => {
            if (say(d)) revealSlip();
          });
        }}
        onType={(p) => {
          setTyping(p);
        }}
      />
      {typing ? <TypePriceDialog provider={typing} market={market} side={side} actions={bet.actions} onClose={() => { setTyping(null); }} /> : null}
    </>
  );
}

export function HedgeScreen(props: { route: BetRoute }) {
  const bet = useBet();
  const q = parseCompareQuery(props.route.query);
  const ws = bet.ready ? bet.view.workspace : null;
  const held = ws ? heldPositions(ws.draft) : [];
  const leg = held.find((l) => l.id === q.leg) ?? held[0] ?? null;
  return (
    <section class="ph-bet stack" aria-labelledby="ph-bet-hedge-h">
      <PageHead
        id="ph-bet-hedge-h"
        kicker="Betting tools"
        title={TITLE_HEDGE}
        description="Your exact recorded positions, carried over from the slip. Pick one; the other side of its market is priced at each venue and sized to cover its gross payout."
      />
      <MoneyNote arithmetic />
      {!bet.ready || !ws ? (
        <Skeleton lines={3} label="Loading your slip" />
      ) : leg === null ? (
        <div class="ph-bet-empty">
          <h2>No recorded positions yet</h2>
          <p>The Hedge Finder works from bets you already placed. Mark your slip as placed (it asks you to confirm and only writes a note in this browser), or log a bet you hold in Compare.</p>
          <div class="row wrap">
            <a class="btn primary" href="#/bet/slip">
              Open your slip
            </a>
            <a class="btn" href="#/bet/compare">
              Open Compare
            </a>
          </div>
        </div>
      ) : (
        <Finder key={leg.id} bet={bet} leg={leg} held={held} />
      )}
    </section>
  );
}

export function HedgePage(props: { route: BetRoute }) {
  ensureBetStyles();
  return (
    <MarketGate name="The slip Hedge Finder" what="The slip Hedge Finder sizes hedges on prices.">
      <HedgeScreen route={props.route} />
    </MarketGate>
  );
}
