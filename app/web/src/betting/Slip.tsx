// The rolling slip: picks waiting for a price, recorded positions (locked into exposure), planned legs (switch on/off,
// amount, price), the cost and outcome table and the coverage block from evaluateDraft, the note fields, "Mark slip as
// placed" (a local record, confirmed in a dialog), the way into the Hedge Finder, export/import and the labelled demo.
// Shown in two places: the persistent panel (compact) and the page #/bet/slip (full).
import { useEffect, useRef, useState } from "preact/hooks";
import { Button, Dialog, Skeleton } from "../ui";
import { Icon } from "../icons";
import { count } from "../format";
import { saveTextFile } from "../saveFile";
import { useLens } from "../prefs";
import { DEMO_LABEL, DEMO_NOTE, PLACED_CONFIRM, TITLE_SLIP } from "./copy";
import { activeLegs, evaluateDraft, type SlipEvaluation } from "./slipModel";
import { scopeLabel } from "./store";
import type { SlipPick, SlipWorkspace } from "./types";
import { compareHashFor } from "./viewModel";
import { CoverageBlock, LegCard, MarketGate, MoneyNote, OutcomeTable, SaveState, Warnings, marketWords, money, providerLabel } from "./parts";
import { ensureBetStyles, say, setPanelOpen, useBet, useNow, usePanelOpen, useWide, type BetHook } from "./useBet";

type Ready = Extract<BetHook, { ready: true }>;

/** How many things the slip holds that count or wait: included planned legs, recorded positions, unpriced picks. */
export function slipCount(ws: SlipWorkspace): number {
  return activeLegs(ws.draft).length + ws.picks.length;
}

function PickRow(props: { pick: SlipPick; bet: Ready }) {
  const { pick } = props;
  return (
    <li class="ph-bet-pick">
      <div>
        <strong>{marketWords(pick.market, pick.side)}</strong>
        <span class="small muted"> · {pick.market.eventLabel || pick.market.eventId}</span>
        {pick.market.origin === "sample" ? <span class="small muted"> · sample</span> : null}
      </div>
      <div class="row wrap">
        <a class="btn small" href={compareHashFor(pick.market, pick.side)}>
          Compare prices
        </a>
        <button
          type="button"
          class="icon-btn ph-bet-x"
          aria-label={`Remove ${marketWords(pick.market, pick.side)} from the slip`}
          onClick={() => {
            void props.bet.actions.removePick(pick.id).then((d) => say(d));
          }}
        >
          ×
        </button>
      </div>
    </li>
  );
}

function Totals(props: { ev: SlipEvaluation }) {
  const { ev } = props;
  const providers = Object.keys(ev.providerTotals);
  if (!ev.groups.length) return null;
  return (
    <div class="ph-bet-totals">
      <dl>
        {providers.map((p) => (
          <div key={p}>
            <dt>{providerLabel(p)}</dt>
            <dd class="num">{money(ev.providerTotals[p])}</dd>
          </div>
        ))}
        <div class="total">
          <dt>Total cost</dt>
          <dd class="num">{ev.totalCost === null ? "not complete" : money(ev.totalCost)}</dd>
        </div>
      </dl>
    </div>
  );
}

function EntryTerms(props: { bet: Ready; ws: SlipWorkspace }) {
  const { entry } = props.ws.draft;
  const picks = props.ws.draft.legs.filter((l) => l.quote.price.kind === "entry-pick");
  if (!picks.length) return null;
  const set = (stake: number, multiplier: number): void => {
    void props.bet.actions.setEntry({ stake, multiplier }).then((d) => say(d));
  };
  return (
    <fieldset class="ph-bet-entry">
      <legend>{providerLabel(picks[0]?.quote.provider ?? "prizepicks")} entry: one stake, one all-correct multiplier</legend>
      <p class="small muted">The picks above form one entry. It pays stake × multiplier only if every pick is right; a pick has no price of its own.</p>
      <div class="ph-bet-fields">
        <label>
          <span>Entry stake ($)</span>
          <input key={`s${String(entry.stake)}`} type="number" min={0.01} step={0.01} defaultValue={String(entry.stake)} onChange={(e) => { set(Number(e.currentTarget.value), entry.multiplier); }} />
        </label>
        <label>
          <span>All-correct multiplier (stake included)</span>
          <input key={`m${String(entry.multiplier)}`} type="number" min={1} step={0.1} defaultValue={String(entry.multiplier)} onChange={(e) => { set(entry.stake, Number(e.currentTarget.value)); }} />
        </label>
      </div>
    </fieldset>
  );
}

function Notes(props: { bet: Ready; ws: SlipWorkspace }) {
  const d = props.ws.draft;
  const set = (n: { title?: string; thesis?: string; tags?: string[] }): void => {
    void props.bet.actions.setNotes(n).then((r) => say(r));
  };
  return (
    <details class="ph-bet-notes" open={d.title !== "" || d.thesis !== "" ? true : undefined}>
      <summary>The thinking behind this slip</summary>
      <p class="small muted">Kept with the slip in this browser. Saving it as an idea comes with the Ideas screen.</p>
      <label>
        <span>Title</span>
        <input key={`t${d.title}`} maxLength={200} defaultValue={d.title} placeholder="e.g. Cover my rebound over" onChange={(e) => { set({ title: e.currentTarget.value }); }} />
      </label>
      <label>
        <span>Thesis</span>
        <textarea key={`h${d.thesis}`} rows={3} maxLength={5000} defaultValue={d.thesis} placeholder="What would make this wrong?" onChange={(e) => { set({ thesis: e.currentTarget.value }); }} />
      </label>
      <label>
        <span>Tags (comma separated)</span>
        <input key={`g${d.tags.join(",")}`} defaultValue={d.tags.join(", ")} placeholder="hedge, rotation" onChange={(e) => { set({ tags: e.currentTarget.value.split(",") }); }} />
      </label>
    </details>
  );
}

/** "Mark slip as placed": a confirmation that says exactly what it does, then a local record. */
function PlaceButton(props: { bet: Ready; ws: SlipWorkspace; ev: SlipEvaluation; onPlaced?: () => void }) {
  const [open, setOpen] = useState(false);
  const planned = props.ws.draft.legs.filter((l) => l.role === "planned" && l.enabled);
  if (!planned.length) return null;
  return (
    <>
      <Button kind="primary" wide onClick={() => { setOpen(true); }}>
        Mark slip as placed
      </Button>
      {open ? (
        <Dialog title="Mark slip as placed?" id="ph-bet-place" onClose={() => { setOpen(false); }}>
          <p>{PLACED_CONFIRM}</p>
          <ul class="small">
            {planned.map((l) => (
              <li key={l.id}>
                {marketWords(l.market, l.quote.side)} at {providerLabel(l.quote.provider)}
              </li>
            ))}
          </ul>
          {!props.ev.complete ? <p class="small ph-bet-err">Some legs are not fully priced (an unknown fee or amount). They are recorded as they are.</p> : null}
          <div class="row wrap">
            <Button onClick={() => { setOpen(false); }}>Cancel</Button>
            <Button
              kind="primary"
              onClick={() => {
                void props.bet.actions.markPlaced().then((d) => {
                  setOpen(false);
                  if (say(d)) props.onPlaced?.();
                });
              }}
            >
              I already placed these
            </Button>
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

function Tools(props: { bet: Ready; ws: SlipWorkspace }) {
  const [confirm, setConfirm] = useState<null | "demo" | { text: string }>(null);
  const [err, setErr] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const { bet, ws } = props;
  return (
    <section class="ph-bet-tools" aria-label="Workspace">
      <h2>Keep or move your slip</h2>
      <p class="small muted">Kept in this browser only, for {scopeLabel(bet.scope)}. Export a file to keep a copy or move it to another browser.</p>
      <div class="row wrap">
        <Button
          onClick={() => {
            void bet.store.exportJson().then(async (r) => {
              if (!r.ok) {
                setErr(r.text);
                return;
              }
              const s = await saveTextFile(r.filename, r.json, "application/json");
              if (!s.ok) setErr(s.reason);
            });
          }}
        >
          <Icon name="download" /> Export
        </Button>
        <Button onClick={() => file.current?.click()}>Import a file</Button>
        <input
          ref={file}
          type="file"
          accept=".json,application/json"
          class="sr"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const f = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (!f) return;
            void f.text().then((text) => { setConfirm({ text }); }, () => { setErr("That file could not be read. Nothing was changed."); });
          }}
        />
        <Button
          onClick={() => {
            if (ws.draft.legs.length) setConfirm("demo");
            else void bet.actions.loadDemo().then((d) => say(d));
          }}
        >
          {DEMO_LABEL}
        </Button>
      </div>
      <p class="small muted">{DEMO_NOTE}</p>
      {err ? (
        <p class="ph-bet-err small" role="alert">
          {err}
        </p>
      ) : null}
      {confirm === "demo" ? (
        <Dialog title="Replace the working slip with the demo?" onClose={() => { setConfirm(null); }}>
          <p>The demo replaces the legs in your working slip. Picks waiting for a price and anything saved stay. {DEMO_NOTE}</p>
          <div class="row wrap">
            <Button onClick={() => { setConfirm(null); }}>Keep my slip</Button>
            <Button kind="primary" onClick={() => { void bet.actions.loadDemo().then((d) => { setConfirm(null); say(d); }); }}>
              Load the demo
            </Button>
          </div>
        </Dialog>
      ) : confirm !== null ? (
        <Dialog title="Replace this browser's workspace with the file?" onClose={() => { setConfirm(null); }}>
          <p>Everything in this browser's betting workspace (slip, typed prices, picks) is replaced by the file. If the file does not pass its check, nothing changes and you are told why.</p>
          <div class="row wrap">
            <Button onClick={() => { setConfirm(null); }}>Cancel</Button>
            <Button
              kind="primary"
              onClick={() => {
                void bet.store.importJson(confirm.text).then((d) => {
                  setConfirm(null);
                  if (d.ok) {
                    setErr(null);
                    say(d);
                  } else setErr(`Not imported: ${d.text}`);
                });
              }}
            >
              Replace with the file
            </Button>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}

/** The slip itself. `compact` is the panel; the page shows every outcome, the notes and the workspace tools. */
export function SlipBody(props: { bet: Ready; compact?: boolean }) {
  const { bet } = props;
  const { view } = bet;
  const now = useNow();
  const hedgeLink = useRef<HTMLAnchorElement>(null);
  const [placed, setPlaced] = useState(0);
  useEffect(() => {
    if (placed) hedgeLink.current?.focus();
  }, [placed]);
  const save = (
    <SaveState
      view={view}
      onRetry={() => { void bet.store.retry().then((d) => say(d)); }}
      onDiscard={() => { bet.store.discard(); }}
      onReload={() => { void bet.store.load({ discardLocal: true }); }}
    />
  );
  const ws = view.workspace;
  if (!ws) return view.phase === "error" ? save : <Skeleton lines={3} label="Loading your slip" />;
  const ev = evaluateDraft(ws.draft, { now });
  const recorded = ws.draft.legs.filter((l) => l.role === "existing");
  const planned = ws.draft.legs.filter((l) => l.role === "planned");
  const empty = !ws.draft.legs.length && !ws.picks.length;
  return (
    <div class="ph-bet-slip">
      {save}
      <MoneyNote arithmetic />
      <p class="small muted num">
        {count(planned.filter((l) => l.enabled).length)} of {count(planned.length)} planned legs included · {count(recorded.length)} recorded · {count(ws.picks.length)} waiting for a price.
        {ws.draft.origin === "sample" ? " This slip is the labelled demo: sample prices only." : ""}
      </p>
      {empty ? (
        <div class="ph-bet-empty">
          <h3>Your slip is empty</h3>
          <p>Add a player from a game or player page (the Market lens shows an "Add to slip" button), or open Compare to pick a prop from tonight's slate or type your own.</p>
          <div class="row wrap">
            <a class="btn primary" href="#/bet/compare">
              Open Compare
            </a>
            {props.compact ? (
              <a class="btn" href="#/bet/slip">
                Full slip page
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
      {ws.picks.length ? (
        <section aria-label="Waiting for a price">
          <h3 class="ph-bet-h">Waiting for a price</h3>
          <ul class="plain ph-bet-picks">
            {ws.picks.map((p) => (
              <PickRow key={p.id} pick={p} bet={bet} />
            ))}
          </ul>
        </section>
      ) : null}
      {recorded.length ? (
        <section aria-label="Recorded positions">
          <h3 class="ph-bet-h">Recorded positions (always counted)</h3>
          {recorded.map((l) => (
            <LegCard key={l.id} leg={l} actions={bet.actions} now={now} compact={props.compact} />
          ))}
        </section>
      ) : null}
      {planned.length ? (
        <section aria-label="Planned legs">
          <h3 class="ph-bet-h">Planned legs</h3>
          {planned.map((l) => (
            <LegCard key={l.id} leg={l} actions={bet.actions} now={now} compact={props.compact} />
          ))}
        </section>
      ) : null}
      <EntryTerms bet={bet} ws={ws} />
      {ws.draft.legs.length ? (
        <>
          <Totals ev={ev} />
          <CoverageBlock coverage={ev.coverage} />
          <Warnings items={ev.warnings} />
          {ev.overlaps.length ? (
            <ul class="ph-bet-overlaps small">
              {ev.overlaps.map((o) => (
                <li key={`${o.kind}-${o.marketIds.join(",")}`}>
                  <strong>{o.label}.</strong> {o.detail}
                </li>
              ))}
            </ul>
          ) : null}
          <OutcomeTable evaluation={ev} limit={props.compact ? 4 : undefined} />
        </>
      ) : null}
      <div class="ph-bet-actions">
        <PlaceButton bet={bet} ws={ws} ev={ev} onPlaced={() => { setPlaced((n) => n + 1); }} />
        {recorded.length ? (
          <a ref={hedgeLink} class="btn wide" href="#/bet/hedge">
            Open Hedge Finder with these positions
          </a>
        ) : null}
        {props.compact ? (
          <a class="btn quiet wide" href="#/bet/slip">
            Full slip page: every outcome, notes, export
          </a>
        ) : null}
      </div>
      {props.compact ? null : (
        <>
          <Notes bet={bet} ws={ws} />
          <Tools bet={bet} ws={ws} />
        </>
      )}
    </div>
  );
}

/** #/bet/slip */
export function SlipPage() {
  ensureBetStyles();
  const bet = useBet();
  return (
    <MarketGate name="The rolling slip" what="The rolling slip holds prices, costs and payouts.">
      <section class="ph-bet stack" aria-labelledby="ph-bet-slip-h">
        <h1 id="ph-bet-slip-h">{TITLE_SLIP}</h1>
        {bet.ready ? <SlipBody bet={bet} /> : <Skeleton lines={3} label="Waiting to know who is looking" />}
      </section>
    </MarketGate>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** The persistent slip: a collapsible right-hand panel on a wide screen, a bottom sheet (modal) on a phone. Market lens only. */
export function SlipPanel(props: { onBetRoute: boolean }) {
  ensureBetStyles();
  const bet = useBet();
  const open = usePanelOpen();
  const wide = useWide();
  const sheet = useRef<HTMLDivElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(open);
  const lens = useLens();
  const sheetMode = open && !wide && lens === "market";
  useEffect(() => {
    // Back to the launcher when the panel closes (keyboard users land where they started).
    if (wasOpen.current && !open) launcher.current?.focus();
    wasOpen.current = open;
  }, [open]);
  useEffect(() => {
    if (!sheetMode) return;
    sheet.current?.querySelector<HTMLElement>(".ph-bet-panel-close")?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [sheetMode]);
  // A Market-lens tool: in Learn and Explore no part of the slip shows (no prices there).
  if (lens !== "market") return null;
  if (!bet.ready || bet.view.workspace === null) {
    if (!bet.ready || bet.view.phase !== "error") return null;
  }
  const n = bet.view.workspace ? slipCount(bet.view.workspace) : 0;
  const unsaved = bet.view.dirty;
  if (!open) {
    return (
      <div class="ph-bet">
        <button ref={launcher} type="button" class="ph-bet-launcher" aria-expanded="false" aria-controls="ph-bet-panel" aria-label={`Open your slip, ${String(n)} ${n === 1 ? "item" : "items"}${unsaved ? ", not saved" : ""}`} onClick={() => { setPanelOpen(true); }}>
          <Icon name="picks" />
          <span>Slip</span>
          <span class="ph-bet-count num">{n}</span>
          {unsaved ? <span class="ph-bet-dot" aria-hidden="true" /> : null}
        </button>
      </div>
    );
  }
  const onKey = (e: KeyboardEvent): void => {
    if (e.defaultPrevented) return;
    if (e.key === "Escape") {
      e.preventDefault();
      setPanelOpen(false);
      return;
    }
    if (!sheetMode || e.key !== "Tab" || !sheet.current) return;
    const list = [...sheet.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    const first = list[0];
    const last = list[list.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  };
  return (
    <div class="ph-bet">
      {sheetMode ? <div class="ph-bet-scrim" aria-hidden="true" onClick={() => { setPanelOpen(false); }} /> : null}
      <div
        ref={sheet}
        id="ph-bet-panel"
        class={`ph-bet-panel${sheetMode ? " sheet" : " side"}`}
        role={sheetMode ? "dialog" : "complementary"}
        aria-modal={sheetMode ? "true" : undefined}
        aria-labelledby="ph-bet-panel-h"
        onKeyDown={onKey}
      >
        <header class="ph-bet-panel-head">
          <h2 id="ph-bet-panel-h">
            {TITLE_SLIP} <span class="ph-bet-count num">{n}</span>
          </h2>
          <button type="button" class="icon-btn ph-bet-panel-close" aria-label={sheetMode ? "Close your slip" : "Collapse your slip"} onClick={() => { setPanelOpen(false); }}>
            <Icon name={sheetMode ? "close" : "chevron"} />
          </button>
        </header>
        <div class="ph-bet-panel-body">
          <SlipBody bet={bet} compact />
        </div>
        {props.onBetRoute ? null : <p class="small muted ph-bet-panel-foot">Stays with you across pages on this device.</p>}
      </div>
    </div>
  );
}
