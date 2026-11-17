// #/bet/ideas (the list) and #/bet/ideas/<id> (one idea). An idea is the slip as it was when saved: title, thesis, tags and
// an independent frozen snapshot of every leg and price. The list is searchable and filtered by status; an idea's page
// shows the thesis, each leg's "price when saved" beside the newest stored price, the shared exposure in words, every
// listed outcome at the saved prices, the viewer's own paper outcomes (typed by hand, unverified) and the net they give,
// which is arithmetic on the saved prices only. Loading an idea copies it into the slip; the idea never changes.
import { useRef, useState } from "preact/hooks";
import { Badge, Button, Dialog, FilterChips, Skeleton, Tile } from "../ui";
import { count, lineText } from "../format";
import { saveTextFile } from "../saveFile";
import { stamp } from "../time";
import { STAT_LABEL } from "../types";
import type { BetRoute } from "./seam";
import { quoteSlot } from "./catalog";
import { ARITHMETIC_NOTE, PAPER_NET_NOTE, TITLE_IDEAS } from "./copy";
import { IDEA_FILTERS, STATUS_WORD, evaluateIdea, filterIdeas, ideaResult, ideaSummary, ideasFile, ideasFilename, legResult, marketMark, paperTotal, statusCounts, type IdeaFilter, type IdeaResult, type LegResult, type MarketMark } from "./ideaModel";
import { activeLegs, ideaMarketIds } from "./slipModel";
import type { IdeaStatus, Market, SavedIdea, Side, SlipLeg } from "./types";
import { MarketGate, MoneyNote, OutcomeTable, marketWords, money, providerLabel, signedMoney, sideWord } from "./parts";
import { SourceCell, nativePrice, useStored } from "./board";
import { compareHashFor } from "./viewModel";
import { ensureBetStyles, say, useBet, useNow, type BetHook } from "./useBet";

type Ready = Extract<BetHook, { ready: true }>;

const STATUS_TONE: Readonly<Record<IdeaStatus, string>> = { watching: "base", paper: "mine", settled: "model" };
const STATUS_ICON = { watching: "clock", paper: "pen", settled: "check" } as const;

export const ideaHref = (id: string): string => `#/bet/ideas/${encodeURIComponent(id)}`;

function StatusBadge(props: { status: IdeaStatus }) {
  return <Badge tone={STATUS_TONE[props.status]} icon={STATUS_ICON[props.status]} word={STATUS_WORD[props.status]} />;
}

/** The result line of an idea: its paper net (with how it was made), how many markets are recorded, or why there is none. */
function ResultLine(props: { r: IdeaResult; compact?: boolean }) {
  const { r } = props;
  if (r.kind === "net")
    return (
      <div class="ph-bet-net">
        <span class="ph-bet-net-label">Paper net</span>
        <strong class={`num ${r.row.net < 0 ? "ph-bet-neg" : r.row.net > 0 ? "ph-bet-pos" : ""}`}>{signedMoney(r.row.net)}</strong>
        <span class="small muted num">
          {money(r.row.payout)} payout minus {money(r.totalCost)} saved cost.{props.compact ? "" : ` ${PAPER_NET_NOTE}`}
        </span>
      </div>
    );
  if (r.kind === "open")
    return (
      <p class="small muted num">
        Outcomes recorded for {count(r.recorded)} of {count(r.markets)} {r.markets === 1 ? "market" : "markets"}.
      </p>
    );
  return <p class="small muted">{r.text}</p>;
}

// ------------------------------------------------------------------------------------------------ the list

function IdeaRow(props: { idea: SavedIdea; now: number }) {
  const { idea } = props;
  const ev = evaluateIdea(idea, props.now);
  const s = ideaSummary(idea);
  return (
    <article class="ph-bet-idea">
      <div class="ph-bet-idea-head">
        <h2>
          <a href={ideaHref(idea.id)}>{idea.title || "Untitled idea"}</a>
        </h2>
        <StatusBadge status={idea.status} />
        {idea.origin === "sample" ? <Badge tone="none" icon="spark" word="sample" title="Saved from the labelled demo: invented prices" /> : null}
      </div>
      <p class="small muted num">
        Saved {stamp(idea.createdAt)} · {count(idea.legs.length)} {idea.legs.length === 1 ? "leg" : "legs"} ({count(s.included)} included) · {s.players.join(", ")} · {s.venues.map((v) => providerLabel(v)).join(", ")}
      </p>
      {idea.thesis ? <p class="ph-bet-idea-thesis">{idea.thesis}</p> : null}
      {idea.tags.length ? (
        <p class="ph-bet-tags">
          {idea.tags.map((t) => (
            <span key={t} class="ph-bet-tag">
              #{t}
            </span>
          ))}
        </p>
      ) : null}
      <div class="ph-bet-idea-foot">
        {idea.status === "settled" ? (
          <ResultLine r={ideaResult(idea, ev)} compact />
        ) : (
          <p class="small num">
            Saved cost {ev.totalCost === null ? "not complete (an unknown fee or amount)" : money(ev.totalCost)}
            {ev.rows.length ? ` · ${count(ev.rows.length)} listed outcomes` : ""}
          </p>
        )}
        <a class="btn small" href={ideaHref(idea.id)}>
          Open
        </a>
      </div>
    </article>
  );
}

function ImportIdeas(props: { bet: Ready; label: string }) {
  const file = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <>
      <Button onClick={() => file.current?.click()}>{props.label}</Button>
      <input
        ref={file}
        type="file"
        accept=".json,application/json"
        class="sr"
        tabIndex={-1}
        aria-hidden="true"
        data-ph-ideas-import=""
        onChange={(e) => {
          const f = e.currentTarget.files?.[0];
          e.currentTarget.value = "";
          if (!f) return;
          void f.text().then(
            (text) =>
              props.bet.actions.importIdeas(text).then((d) => {
                setErr(d.ok ? null : `Not imported: ${d.text}`);
                say(d, "Ideas");
              }),
            () => {
              setErr("That file could not be read. Nothing was changed.");
            },
          );
        }}
      />
      {err ? (
        <p class="ph-bet-err small ph-bet-full" role="alert">
          {err}
        </p>
      ) : null}
    </>
  );
}

async function exportIdeas(ideas: readonly SavedIdea[], one?: SavedIdea): Promise<void> {
  const at = new Date().toISOString();
  const r = await saveTextFile(ideasFilename(at, one), ideasFile(ideas, at), "application/json");
  say(r.ok ? { ok: true, text: one ? "The idea was exported as a file." : `${String(ideas.length)} ${ideas.length === 1 ? "idea" : "ideas"} exported as a file.` } : { ok: false, code: "save_failed", text: r.reason }, "Ideas");
}

function IdeasList(props: { bet: Ready }) {
  const { bet } = props;
  const now = useNow();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<IdeaFilter>("all");
  const ws = bet.view.workspace;
  if (!ws) return <Skeleton lines={3} label="Loading your ideas" />;
  const counts = statusCounts(ws.ideas);
  const total = paperTotal(ws.ideas, now);
  const shown = filterIdeas(ws.ideas, query, status);
  return (
    <>
      <div class="ph-bet-tiles">
        <Tile label="Saved ideas" value={count(ws.ideas.length)} note="Thesis plus frozen leg and price snapshots" />
        <Tile label="Still open" value={count(counts.watching + counts.paper)} note={`${count(counts.watching)} watching · ${count(counts.paper)} paper`} />
        <Tile
          label="Recorded paper net"
          value={total.n ? signedMoney(total.net) : "—"}
          n={total.n ? `n = ${count(total.n)} settled ${total.n === 1 ? "idea" : "ideas"}` : null}
          note={total.n ? `At saved prices and outcomes you typed (unverified)${total.excluded ? `; ${count(total.excluded)} with a void left out` : ""}` : "No settled idea with a net yet"}
          tone="mine"
        />
      </div>
      <div class="ph-bet-ideas-tools">
        <label class="ph-bet-search">
          <span class="sr">Search saved ideas</span>
          <input type="search" placeholder="Search titles, theses, tags, players, venues" value={query} onInput={(e) => { setQuery(e.currentTarget.value); }} />
        </label>
        <FilterChips
          label="Filter by status"
          options={IDEA_FILTERS.map((f) => ({ value: f, label: f === "all" ? "All" : STATUS_WORD[f], count: count(counts[f]) }))}
          value={status}
          onChange={(v) => { setStatus(IDEA_FILTERS.includes(v as IdeaFilter) ? (v as IdeaFilter) : "all"); }}
        />
        <div class="row wrap ph-bet-ideas-actions">
          <Button disabled={!ws.ideas.length} onClick={() => { void exportIdeas(ws.ideas); }}>
            Export all ideas
          </Button>
          <ImportIdeas bet={bet} label="Import an ideas file" />
        </div>
      </div>
      {!ws.ideas.length ? (
        <div class="ph-bet-empty">
          <h2>No saved ideas yet</h2>
          <p>Build a slip, write down why the legs belong together, and save it as an idea from the slip page. The idea keeps the legs and prices exactly as they were, so you can come back and record what happened.</p>
          <div class="row wrap">
            <a class="btn primary" href="#/bet/slip">
              Open your slip
            </a>
            <a class="btn" href="#/bet/compare">
              Compare venues
            </a>
          </div>
        </div>
      ) : !shown.length ? (
        <div class="ph-bet-empty">
          <h2>No ideas in this view</h2>
          <p>Nothing matches that search and status. Your saved ideas are all still here.</p>
          <Button onClick={() => { setQuery(""); setStatus("all"); }}>Show all ideas</Button>
        </div>
      ) : (
        <div class="ph-bet-idea-list">
          {shown.map((i) => (
            <IdeaRow key={i.id} idea={i} now={now} />
          ))}
        </div>
      )}
      <p class="small muted">Local paper records only, kept in this browser. Shared players and games are described, never scored; nothing here is a measured correlation or an edge.</p>
    </>
  );
}

// ------------------------------------------------------------------------------------------------ one idea

const RESULT_WORD: Readonly<Record<LegResult, string>> = { won: "Won", lost: "Lost", void: "Void", pending: "Pending" };
const RESULT_TONE: Readonly<Record<LegResult, string>> = { won: "better", lost: "worse", void: "none", pending: "base" };

/** The newest stored price for the leg's venue, market and side now, beside the price saved (which never changes). */
function NewestStored(props: { leg: SlipLeg; now: number }) {
  const { leg } = props;
  const st = useStored(leg.market.origin === "export" ? leg.market : null, leg.quote.side);
  if (leg.market.origin === "manual") return <span class="small muted">A typed market: no stored prices</span>;
  if (leg.market.origin === "sample") return <span class="small muted">Demo market: no stored prices</span>;
  if (st.state.kind === "loading") return <span class="small muted">Reading...</span>;
  if (st.state.kind === "error") return <span class="small ph-bet-err">Could not be read</span>;
  if (st.state.kind === "off" || !st.stored) return <span class="small muted">Not available from this server</span>;
  const slot = quoteSlot(st.stored, leg.market.id, leg.quote.provider, leg.quote.side);
  if (slot.state !== "available") return <span class="small muted">No stored price now</span>;
  return (
    <span class="ph-bet-now">
      <span class="num">{nativePrice(slot.quote)}</span> <SourceCell quote={slot.quote} now={props.now} />
    </span>
  );
}

function LegsTable(props: { idea: SavedIdea; now: number }) {
  const { idea } = props;
  const included = new Set(activeLegs(idea).map((l) => l.id));
  return (
    <div class="ph-bet-board-wrap">
      <table class="ph-bet-board ph-bet-idea-legs">
        <caption class="sr">The legs of this idea as saved</caption>
        <thead>
          <tr>
            <th scope="col">Leg</th>
            <th scope="col" class="num">
              Price when saved
            </th>
            <th scope="col">Newest stored price</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {idea.legs.map((l) => {
            const r = included.has(l.id) ? legResult(idea, l) : null;
            return (
              <tr key={l.id}>
                <th scope="row" class="ph-bet-b-venue">
                  <span class="ph-bet-b-name">{marketWords(l.market, l.quote.side)}</span>
                  <span class="small muted ph-bet-b-sub num">
                    {providerLabel(l.quote.provider)} · {l.market.eventLabel || l.market.eventId} · {l.role === "existing" ? "recorded position" : included.has(l.id) ? "planned" : "left out (not counted)"} ·{" "}
                    {l.quote.price.kind === "binary" ? `${count(l.amount)} contracts` : l.quote.price.kind === "sportsbook" ? `${money(l.amount)} stake` : "entry pick"}
                  </span>
                </th>
                <td class="ph-bet-b-price num" data-label="Saved">
                  <span class="ph-bet-b-lab">Saved </span>
                  {nativePrice(l.quote)}
                  <span class="ph-bet-b-sub">
                    <SourceCell quote={l.quote} now={props.now} />
                  </span>
                </td>
                <td class="small" data-label="Newest stored">
                  <span class="ph-bet-b-lab">Newest stored </span>
                  <NewestStored leg={l} now={props.now} />
                </td>
                <td data-label="Result">{r ? <Badge tone={RESULT_TONE[r]} icon={r === "won" ? "check" : r === "lost" ? "close" : r === "void" ? "none" : "clock"} word={RESULT_WORD[r]} /> : <span class="small muted">not counted</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const MARK_WORD = (m: Market, mark: MarketMark): string =>
  mark === "yes" ? `Over ${lineText(m.line)} hit` : mark === "no" ? `Under ${lineText(m.line)} hit` : mark === "void" ? "Void" : "Pending";

/** The viewer's own outcome per market (typed by hand, unverified), the net it gives at the saved prices, and save/settle. */
function OutcomeForm(props: { bet: Ready; idea: SavedIdea; now: number }) {
  const { idea, bet } = props;
  const ids = ideaMarketIds(idea);
  const markets = ids.map((id) => idea.legs.find((l) => l.market.id === id)?.market).filter((m): m is Market => m !== undefined);
  const [marks, setMarks] = useState<Record<string, MarketMark>>(() => Object.fromEntries(ids.map((id) => [id, marketMark(idea, id)])));
  const [confirmReopen, setConfirmReopen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const outcomes: Record<string, Side> = {};
  const voids: string[] = [];
  for (const id of ids) {
    const m = marks[id] ?? "pending";
    if (m === "yes" || m === "no") outcomes[id] = m;
    else if (m === "void") voids.push(id);
  }
  const preview: SavedIdea = { ...idea, outcomes, ...(voids.length ? { voids } : {}) };
  const pr = ideaResult(preview, evaluateIdea(preview, props.now));
  const all = ids.every((id) => (marks[id] ?? "pending") !== "pending");
  const save = (status: IdeaStatus): void => {
    void bet.actions.recordOutcomes(idea.id, outcomes, voids, status).then((d) => {
      setErr(d.ok ? null : d.text);
      say(d, "Idea");
    });
  };
  if (idea.status === "settled") {
    return (
      <section class="ph-bet-outcome" aria-labelledby="ph-bet-out-h">
        <h2 class="ph-bet-h" id="ph-bet-out-h">
          Your recorded outcomes
        </h2>
        <ul class="plain ph-bet-marks">
          {markets.map((m) => (
            <li key={m.id}>
              <span>
                {m.playerName} {STAT_LABEL[m.stat].toLowerCase()} {lineText(m.line)}
              </span>{" "}
              <strong>{MARK_WORD(m, marketMark(idea, m.id))}</strong>
            </li>
          ))}
        </ul>
        <ResultLine r={ideaResult(idea, evaluateIdea(idea, props.now))} />
        <Button onClick={() => { setConfirmReopen(true); }}>Reopen this idea</Button>
        {confirmReopen ? (
          <Dialog title="Reopen this idea?" onClose={() => { setConfirmReopen(false); }}>
            <div class="ph-bet">
              <p>Its recorded outcomes are cleared and its status goes back to Paper. The saved legs and prices stay as they are.</p>
              <div class="row wrap">
                <Button onClick={() => { setConfirmReopen(false); }}>Keep it settled</Button>
                <Button
                  kind="primary"
                  onClick={() => {
                    void bet.actions.recordOutcomes(idea.id, {}, [], "paper").then((d) => {
                      setConfirmReopen(false);
                      say(d, "Idea");
                    });
                  }}
                >
                  Reopen and clear the outcomes
                </Button>
              </div>
            </div>
          </Dialog>
        ) : null}
      </section>
    );
  }
  return (
    <section class="ph-bet-outcome" aria-labelledby="ph-bet-out-h">
      <h2 class="ph-bet-h" id="ph-bet-out-h">
        Record what happened
      </h2>
      <p class="small muted">Typed by you, from the box score: a paper record, never a venue's settlement. Pending markets can wait; settling needs every market.</p>
      {markets.map((m) => (
        <fieldset key={m.id} class="ph-bet-markset">
          <legend>
            {m.playerName} {STAT_LABEL[m.stat].toLowerCase()} {lineText(m.line)} <span class="small muted">· {m.eventLabel || m.eventId}</span>
          </legend>
          <div class="ph-bet-seg" role="radiogroup" aria-label={`Outcome for ${m.playerName} ${STAT_LABEL[m.stat].toLowerCase()} ${lineText(m.line)}`}>
            {(["yes", "no", "void", "pending"] as const).map((k) => (
              <label key={k} class={`ph-bet-radio${(marks[m.id] ?? "pending") === k ? " on" : ""}`}>
                <input
                  type="radio"
                  name={`mk-${idea.id}-${m.id}`}
                  checked={(marks[m.id] ?? "pending") === k}
                  onChange={() => {
                    setMarks({ ...marks, [m.id]: k });
                  }}
                />
                <span>{MARK_WORD(m, k)}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <ResultLine r={pr} />
      {err ? (
        <p class="ph-bet-err small" role="alert">
          {err}
        </p>
      ) : null}
      <div class="row wrap">
        <Button kind="primary" disabled={!all} onClick={() => { save("settled"); }}>
          Settle with these outcomes
        </Button>
        <Button onClick={() => { save(idea.status === "watching" ? "watching" : "paper"); }}>Save and keep open</Button>
        {idea.status === "watching" ? <Button onClick={() => { save("paper"); }}>Mark as a paper idea</Button> : <Button onClick={() => { save("watching"); }}>Back to watching</Button>}
      </div>
      {!all ? <p class="small muted">Settling needs an outcome (or void) for every market.</p> : null}
    </section>
  );
}

function IdeaActions(props: { bet: Ready; idea: SavedIdea }) {
  const { bet, idea } = props;
  const ws = bet.view.workspace;
  const [confirm, setConfirm] = useState<null | "load" | "delete">(null);
  const load = (): void => {
    void bet.actions.loadIdea(idea.id).then((d) => {
      setConfirm(null);
      if (say(d, "Idea")) window.location.hash = "#/bet/slip";
    });
  };
  return (
    <section class="ph-bet-idea-actions" aria-label="What to do with this idea">
      <div class="row wrap">
        <Button kind="primary" onClick={() => { if (ws?.draft.legs.length) setConfirm("load"); else load(); }}>
          Load a copy into the slip
        </Button>
        <Button onClick={() => { void exportIdeas([idea], idea); }}>Export this idea</Button>
        <Button onClick={() => { setConfirm("delete"); }}>Delete</Button>
      </div>
      {confirm === "load" ? (
        <Dialog title="Replace your working slip?" onClose={() => { setConfirm(null); }}>
          <div class="ph-bet">
            <p>Your working slip ({count(ws?.draft.legs.length ?? 0)} legs) is replaced by a copy of this idea. The saved idea stays exactly as it is; picks waiting for a price and other ideas stay too.</p>
            <div class="row wrap">
              <Button onClick={() => { setConfirm(null); }}>Cancel</Button>
              <Button kind="primary" onClick={load}>
                Replace and open the slip
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
      {confirm === "delete" ? (
        <Dialog title="Delete this idea?" onClose={() => { setConfirm(null); }}>
          <div class="ph-bet">
            <p>This removes the idea, its thesis and its recorded outcomes from this browser. Export it first if you want a copy. Nothing at any venue is touched.</p>
            <div class="row wrap">
              <Button onClick={() => { setConfirm(null); }}>Keep it</Button>
              <Button
                kind="primary"
                onClick={() => {
                  void bet.actions.removeIdea(idea.id).then((d) => {
                    setConfirm(null);
                    if (say(d, "Idea")) window.location.hash = "#/bet/ideas";
                  });
                }}
              >
                Delete the idea
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}

function IdeaPage(props: { bet: Ready; id: string }) {
  const now = useNow();
  const ws = props.bet.view.workspace;
  if (!ws) return <Skeleton lines={3} label="Loading the idea" />;
  const idea = ws.ideas.find((i) => i.id === props.id);
  if (!idea)
    return (
      <div class="ph-bet-empty">
        <h1 id="ph-bet-ideas-h">That idea is not saved in this browser</h1>
        <p>It may have been deleted, or the link came from another browser. Ideas live only where they were saved (export and import move them).</p>
        <a class="btn primary" href="#/bet/ideas">
          All ideas
        </a>
      </div>
    );
  const ev = evaluateIdea(idea, now);
  const firstExport = idea.legs.find((l) => l.market.origin !== "manual");
  return (
    <>
      <div class="ph-bet-idea-title">
        <h1 id="ph-bet-ideas-h">{idea.title || "Untitled idea"}</h1>
        <StatusBadge status={idea.status} />
        {idea.origin === "sample" ? <Badge tone="none" icon="spark" word="sample" title="Saved from the labelled demo: invented prices" /> : null}
      </div>
      <p class="small muted num">
        Saved {stamp(idea.createdAt)} · {count(idea.legs.length)} {idea.legs.length === 1 ? "leg" : "legs"} · prices frozen at save time
      </p>
      <MoneyNote arithmetic />
      <section aria-label="Thesis" class="ph-bet-thesis">
        <p>{idea.thesis || <span class="muted">No thesis was saved with this idea.</span>}</p>
        {idea.tags.length ? (
          <p class="ph-bet-tags">
            {idea.tags.map((t) => (
              <span key={t} class="ph-bet-tag">
                #{t}
              </span>
            ))}
          </p>
        ) : null}
      </section>
      <IdeaActions bet={props.bet} idea={idea} />
      <h2 class="ph-bet-h">The legs, as saved</h2>
      <LegsTable idea={idea} now={now} />
      <p class="small muted">"Price when saved" never changes. "Newest stored price" is today's stored snapshot for the same venue, line and side, for reference only.{firstExport ? " " : ""}
        {firstExport ? <a href={compareHashFor(firstExport.market, firstExport.quote.side)}>Compare this market now</a> : null}
      </p>
      <h2 class="ph-bet-h">Shared exposure</h2>
      {ev.overlaps.length ? (
        <ul class="ph-bet-overlaps small">
          {ev.overlaps.map((o) => (
            <li key={`${o.kind}-${o.marketIds.join(",")}`}>
              <strong>{o.label}.</strong> {o.detail}
            </li>
          ))}
        </ul>
      ) : (
        <p class="small">No two included legs share a market, a player or a game.</p>
      )}
      <OutcomeForm key={`${idea.id}-${idea.status}-${JSON.stringify(idea.outcomes)}-${(idea.voids ?? []).join(",")}`} bet={props.bet} idea={idea} now={now} />
      <h2 class="ph-bet-h">Every listed outcome at the saved prices</h2>
      <p class="small muted">{ARITHMETIC_NOTE} These are the prices saved with the idea, not today's.</p>
      {ev.rows.length ? <OutcomeTable evaluation={ev} caption="Every listed outcome at the saved prices" /> : <p class="small">No outcome table: {ev.warnings[0] ?? "a leg is incomplete."}</p>}
      <p class="small muted">
        Side words: {sideWord("yes")} is the stat finishing above the line, {sideWord("no")} below it.
      </p>
    </>
  );
}

export function IdeasPage(props: { route: BetRoute }) {
  ensureBetStyles();
  const bet = useBet();
  const id = props.route.path[1] ?? null;
  return (
    <MarketGate name="Ideas" what="Ideas keep saved prices, costs and paper results.">
      <section class="ph-bet stack" aria-labelledby="ph-bet-ideas-h">
        {id ? (
          <p class="small">
            <a href="#/bet/ideas">All ideas</a>
          </p>
        ) : null}
        {id ? null : <h1 id="ph-bet-ideas-h">{TITLE_IDEAS}</h1>}
        {id ? null : <p class="ph-bet-lede">Your reasoning, kept with the legs and prices exactly as they were when you saved them. Come back after the game and record what happened.</p>}
        {id ? null : <MoneyNote arithmetic />}
        {!bet.ready ? <Skeleton lines={3} label="Waiting to know who is looking" /> : id ? <IdeaPage bet={bet} id={id} /> : <IdeasList bet={bet} />}
      </section>
    </MarketGate>
  );
}
