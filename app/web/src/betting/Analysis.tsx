// #/bet/analysis?game=..&player=..&stat=..&line=..&side=..: a read-only view of one prop chosen from the slip or Compare.
// Every panel is a stored field or an existing component, embedded as it is, so every display lock and lens rule holds:
//   1. the model's forecast: the player page's own Headline, ModelPanel and DistributionPanel, fed by the same
//      /api/breakdown answer the player page reads (nothing re-derived here);
//   2. the stored record for this prop (#/source/...);
//   3. the stored quote board in counts: lines, venues on this line by side, unusable quotes, the newest snapshot time and
//      provenance (no price arithmetic, no ranking);
//   4. this prop in your own workspace: slip legs, recorded positions and saved ideas (local records).
// A panel whose field is missing is left out entirely; nothing is filled with placeholders or sample numbers.
import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { failureMessage, failureOf, type ApiFailure } from "../errors";
import { count, lineText, longDate } from "../format";
import { ladderAt } from "../leg";
import type { LegBreakdown, LegStat, Side as PageSide } from "../legTypes";
import { stamp, agoText } from "../time";
import { PageHead, Skeleton } from "../ui";
import { DistributionPanel } from "../Distribution";
import { Headline } from "../pages/LegLine";
import { ModelPanel } from "../pages/LegHistory";
import type { BetRoute } from "./seam";
import { TITLE_ANALYSIS } from "./copy";
import { MarketGate, MoneyNote, providerLabel } from "./parts";
import { MarketHead, MarketProblem, useMarketFromQuery, useRawBoard } from "./board";
import { ideaHref } from "./Ideas";
import { sideOf, type ApiBoard } from "./snapshots";
import type { Market, SlipWorkspace } from "./types";
import { compareHashFor, parseCompareQuery } from "./viewModel";
import { ensureBetStyles, useBet, useNow } from "./useBet";

const MODEL_STATS: readonly string[] = ["pts", "reb", "ast", "fg3m"];

/** The player page's own model panels for this prop and line, from the same stored breakdown. */
function ModelPanels(props: { market: Market; side: PageSide }) {
  const { market, side } = props;
  const pid = Number(market.playerId);
  const [data, setData] = useState<LegBreakdown | null>(null);
  const [err, setErr] = useState<ApiFailure | null>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    setErr(null);
    api.breakdown(market.eventId, pid, market.stat, market.line, side).then(
      (r) => {
        if (!live) return;
        const body = r.data as LegBreakdown | null;
        if (body === null || typeof body !== "object" || !Array.isArray(body.ladder)) setErr(failureOf(new Error("the breakdown answer was not in the expected form")));
        else setData(body);
      },
      (e: unknown) => {
        if (live) setErr(failureOf(e));
      },
    );
    return () => {
      live = false;
    };
  }, [market.eventId, pid, market.stat, market.line, side]);
  if (err)
    return (
      <p class="ph-bet-err small" role="alert">
        The model's forecast could not be read: {failureMessage(err)}
      </p>
    );
  if (!data) return <Skeleton lines={3} label="Loading the model's forecast" />;
  const row = ladderAt(data.ladder, market.line);
  const modelStat = data.model.available && MODEL_STATS.includes(data.stat);
  return (
    <div class="stack ph-bet-embed">
      {data.prior_games > 0 ? <Headline data={data} row={row} side={side} line={market.line} fresh market /> : null}
      <ModelPanel data={data} side={side} line={market.line} row={row} />
      {modelStat ? <DistributionPanel gameId={data.game_id} playerId={data.player_id} stat={data.stat} line={market.line} /> : null}
    </div>
  );
}

/** The stored quote board in counts only: what is stored, when, and from where. */
function BoardSummary(props: { board: ApiBoard; market: Market; now: number }) {
  const { board, market } = props;
  const all = board.lines.flatMap((l) => l.quotes);
  const here = board.lines.find((l) => l.line === market.line)?.quotes ?? [];
  const venues = (side: "over" | "under"): number => new Set(here.filter((q) => q.side === side && q.usable).map((q) => q.provider)).size;
  const unusable = here.filter((q) => !q.usable).length;
  const newest = all.map((q) => q.snapshot_utc).sort().pop() ?? null;
  const age = newest ? agoText(newest, new Date(props.now)) : null;
  if (!all.length) return <p class="small">No stored quotes for this player and stat. {board.note}</p>;
  return (
    <dl class="ph-bet-facts">
      <div>
        <dt>Provenance</dt>
        <dd>{board.provenance} (stored snapshots from the nightly export, not live)</dd>
      </div>
      <div>
        <dt>Newest snapshot</dt>
        <dd class="num">
          {newest ? stamp(newest) : "not reported"}
          {age ? ` (${age})` : ""}
        </dd>
      </div>
      <div>
        <dt>Lines stored</dt>
        <dd class="num">
          {count(board.lines.length)} ({board.lines.map((l) => lineText(l.line)).join(", ")})
        </dd>
      </div>
      <div>
        <dt>Venues on {lineText(market.line)}</dt>
        <dd class="num">
          {count(venues("over"))} over · {count(venues("under"))} under{unusable ? ` · ${count(unusable)} stored quotes flagged unusable` : ""}
        </dd>
      </div>
      <div>
        <dt>Venues with nothing stored</dt>
        <dd class="num">{board.providers_absent.length ? `${count(board.providers_absent.length)}: ${board.providers_absent.map((p) => providerLabel(p)).join(", ")}` : "none"}</dd>
      </div>
      <div>
        <dt>Not in the export</dt>
        <dd>{board.unknown.join(", ").replace(/_/g, " ")}</dd>
      </div>
    </dl>
  );
}

/** This prop (exact line) in the viewer's own workspace: slip legs and saved ideas. */
function InWorkspace(props: { ws: SlipWorkspace; market: Market }) {
  const legs = props.ws.draft.legs.filter((l) => l.market.id === props.market.id);
  const ideas = props.ws.ideas.filter((i) => i.legs.some((l) => l.market.id === props.market.id));
  if (!legs.length && !ideas.length) return <p class="small">Nothing in your slip or saved ideas is on this line yet.</p>;
  return (
    <ul class="ph-bet-overlaps small">
      {legs.length ? (
        <li>
          Slip: {count(legs.filter((l) => l.role === "planned").length)} planned, {count(legs.filter((l) => l.role === "existing").length)} recorded ({legs.map((l) => `${providerLabel(l.quote.provider)} ${l.quote.side === "yes" ? "over" : "under"}`).join(", ")}). <a href="#/bet/slip">Open the slip</a>
        </li>
      ) : null}
      {ideas.map((i) => (
        <li key={i.id}>
          Saved idea: <a href={ideaHref(i.id)}>{i.title || "Untitled idea"}</a> ({i.status})
        </li>
      ))}
    </ul>
  );
}

function AnalysisScreen(props: { route: BetRoute }) {
  const bet = useBet();
  const now = useNow();
  const q = parseCompareQuery(props.route.query);
  const ws = bet.ready ? bet.view.workspace : null;
  const ms = useMarketFromQuery({ ...q, market: null }, ws);
  const market = ms.kind === "ready" ? ms.market : null;
  const [board] = useRawBoard(market?.eventId ?? "", market?.playerId ?? "", market?.stat ?? "");
  if (ms.kind === "none")
    return (
      <div class="ph-bet-empty">
        <h2>Choose a prop first</h2>
        <p>Analysis opens from a prop in Compare or in your slip. It shows the model's stored forecast for it, its stored record and the stored quote board.</p>
        <a class="btn primary" href="#/bet/compare">
          Open Compare
        </a>
      </div>
    );
  if (ms.kind === "loading") return <Skeleton lines={3} label="Loading the prop" />;
  if (ms.kind === "error") return <MarketProblem text={ms.text} {...(ms.failure ? { failure: ms.failure } : {})} />;
  const m = ms.market;
  if (m.origin !== "export")
    return (
      <div class="ph-bet-empty">
        <h2>No stored forecast for this market</h2>
        <p>Analysis covers props from this app's own data. A market you typed, or the labelled demo, has no stored forecast, record or quote board.</p>
        <a class="btn" href="#/bet/compare">
          Pick a prop in Compare
        </a>
      </div>
    );
  const pageSide: PageSide = q.side;
  const stat = m.stat as LegStat;
  return (
    <>
      <MarketHead market={m} side={sideOf(pageSide)}>
        <p class="small row wrap ph-bet-links">
          <a href={compareHashFor(m, sideOf(pageSide))}>Compare venues</a>
          <a href={`#/leg/${encodeURIComponent(m.eventId)}/${m.playerId}/${stat}${pageSide === "under" ? "?side=under" : ""}`}>Player page</a>
          <a href={`#/source/${encodeURIComponent(m.eventId)}/${m.playerId}/${stat}`}>Stored record for this prop</a>
        </p>
      </MarketHead>
      <section class="ph-bet-panel-card" aria-labelledby="ph-bet-an-model">
        <h2 class="ph-bet-h" id="ph-bet-an-model">
          The model's forecast (as stored)
        </h2>
        <p class="small muted">The player page's own panels for {lineText(m.line)}, {longDate(m.eventDate)}. The nba model is the only source of these numbers; this screen adds none.</p>
        <ModelPanels market={m} side={pageSide} />
      </section>
      <section class="ph-bet-panel-card" aria-labelledby="ph-bet-an-board">
        <h2 class="ph-bet-h" id="ph-bet-an-board">
          The stored quote board
        </h2>
        {board.kind === "loading" ? (
          <Skeleton lines={2} label="Reading stored snapshots" />
        ) : board.kind === "error" ? (
          <p class="ph-bet-err small" role="alert">
            Stored snapshots could not be read: {failureMessage(board.failure)}
          </p>
        ) : board.kind === "off" ? (
          <p class="small">Stored snapshots are not available from this server.</p>
        ) : (
          <BoardSummary board={board.data} market={m} now={now} />
        )}
      </section>
      {ws ? (
        <section class="ph-bet-panel-card" aria-labelledby="ph-bet-an-ws">
          <h2 class="ph-bet-h" id="ph-bet-an-ws">
            In your workspace
          </h2>
          <InWorkspace ws={ws} market={m} />
        </section>
      ) : null}
    </>
  );
}

export function AnalysisPage(props: { route: BetRoute }) {
  ensureBetStyles();
  return (
    <MarketGate name="Prop analysis" what="Prop analysis shows stored prices beside the model's forecast.">
      <section class="ph-bet stack" aria-labelledby="ph-bet-an-h">
        <PageHead
          id="ph-bet-an-h"
          kicker="Betting tools"
          title={TITLE_ANALYSIS}
          description="The model's stored forecast for one prop, with the stored prices beside it as counts. Nothing here is a pick."
        />
        <MoneyNote />
        <AnalysisScreen route={props.route} />
      </section>
    </MarketGate>
  );
}
