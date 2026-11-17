// #/bet/hedge?leg=<id>: Compare with intent "hedge". It starts from the exact positions recorded in the slip (ids,
// prices, sides, amounts), lets the viewer pick any held position, takes the opposite side of that market, and sizes a
// hedge at each venue against the position's uncovered gross payout (hedgeMath via slipModel: whole contracts rounded up
// at an exchange, stake to the cent at a sportsbook). Before/after outcome tables come from evaluateDraft.
import { useState } from "preact/hooks";
import { Button, Skeleton } from "../ui";
import { count } from "../format";
import type { BetRoute } from "./seam";
import { ARITHMETIC_NOTE, SETTLEMENT_NOTE, TITLE_HEDGE } from "./copy";
import { legCost, makeHedgeLeg } from "./slipModel";
import type { Market, Quote, Side, SlipLeg } from "./types";
import { hedgeCards, hedgeSideFor, heldPositions, parseCompareQuery, previewWithLeg } from "./viewModel";
import { CoverageBlock, MarketGate, MoneyNote, OutcomeTable, marketWords, money, providerLabel } from "./parts";
import { MarketHead, SnapshotStatus, VenueCardView, useStored } from "./board";
import { ensureBetStyles, revealSlip, say, setPanelOpen, useBet, useNow, type BetHook } from "./useBet";

type Ready = Extract<BetHook, { ready: true }>;

function HeldChips(props: { held: readonly SlipLeg[]; selected: string }) {
  return (
    <div class="ph-bet-held" role="group" aria-label="Held positions">
      {props.held.map((l) => (
        <a key={l.id} class="btn" href={`#/bet/hedge?leg=${encodeURIComponent(l.id)}`} aria-pressed={l.id === props.selected ? "true" : "false"} aria-current={l.id === props.selected ? "true" : undefined}>
          <span>
            <strong>{marketWords(l.market, l.quote.side)}</strong>
            <br />
            <span class="small">
              {providerLabel(l.quote.provider)} · {l.quote.price.kind === "binary" ? `${count(l.amount)} contracts` : `${money(l.amount)} stake`} · cost {money(legCost(l))}
              {l.quote.provenance === "sample" ? " · sample" : ""}
            </span>
          </span>
        </a>
      ))}
    </div>
  );
}

function Preview(props: { bet: Ready; market: Market; quote: Quote; now: number }) {
  const ws = props.bet.view.workspace;
  if (!ws) return null;
  const sized = makeHedgeLeg(ws.draft, props.quote, props.market);
  const amount = sized.ok ? sized.leg.amount : undefined;
  const p = previewWithLeg(ws.draft, props.quote, props.market, amount, props.now);
  return (
    <section class="ph-bet-preview" aria-label="Before and after">
      <h2 class="ph-bet-h">Before and after this hedge at {providerLabel(props.quote.provider)}</h2>
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
    </section>
  );
}

function Finder(props: { bet: Ready; leg: SlipLeg; held: readonly SlipLeg[] }) {
  const { bet, leg } = props;
  const now = useNow();
  const market = leg.market;
  const side: Side = hedgeSideFor(leg);
  const st = useStored(market, side);
  const [preview, setPreview] = useState<Quote | null>(null);
  const ws = bet.view.workspace;
  if (!ws) return null;
  const cards = hedgeCards({ providers: st.providers, stored: st.stored, workspace: ws, market, side, now });
  const unusable = (provider: string): string[] => st.board?.unusable.filter((u) => u.provider === provider).map((u) => u.reason) ?? [];
  return (
    <>
      <HeldChips held={props.held} selected={leg.id} />
      <MarketHead market={market} side={side}>
        <p class="small">
          Hedging your {providerLabel(leg.quote.provider)} {leg.quote.side === "yes" ? "over" : "under"} position: the other side is shown at every venue.
        </p>
      </MarketHead>
      <SnapshotStatus state={st.state} source={st.source} board={st.board} />
      <p class="small" role="note">
        {SETTLEMENT_NOTE}
      </p>
      <div class="ph-bet-cards">
        {cards.map((c) => (
          <VenueCardView
            key={c.provider.id}
            card={c}
            market={market}
            side={side}
            now={now}
            actions={bet.actions}
            crowned={false}
            unusable={unusable(c.provider.id)}
            addLabel={`Add ${c.provider.label} hedge to slip`}
            extra={
              c.hedge ? (
                <div class="small">
                  <p>
                    <strong>{c.hedge.text}</strong>
                  </p>
                  {c.active ? (
                    <button type="button" class="linklike small" onClick={() => { setPreview(c.active); }}>
                      Show before and after
                    </button>
                  ) : null}
                </div>
              ) : null
            }
            onAdd={(q) => {
              const sized = makeHedgeLeg(ws.draft, q, market);
              const amount = sized.ok ? sized.leg.amount : undefined;
              void bet.actions.addQuote(q, market, amount).then((d) => {
                if (say(d)) {
                  setPreview(q);
                  revealSlip();
                }
              });
            }}
          />
        ))}
      </div>
      {preview ? <Preview bet={bet} market={market} quote={preview} now={now} /> : <p class="small muted">Choose "Show before and after" on a venue to see every listed outcome with and without that hedge.</p>}
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
      <h1 id="ph-bet-hedge-h">{TITLE_HEDGE}</h1>
      <p class="ph-bet-lede">Your exact recorded positions, carried over from the slip. Pick one; the other side of its market is priced at each venue and sized to cover its gross payout.</p>
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
            <Button onClick={() => { setPanelOpen(true); }}>Show the slip panel</Button>
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
